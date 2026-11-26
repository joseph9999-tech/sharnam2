import requests
from nornir import InitNornir
from nornir.core.task import Task, Result
import logging
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result
from nornir import InitNornir
from ns_nornir_table_inventory.plugins.inventory import FlatDataInventory
from nornir.core.plugins.inventory import InventoryPluginRegister
InventoryPluginRegister.register("FlatDataInventory", FlatDataInventory)
from nornir_scrapli.tasks import netconf_edit_config, netconf_get_config

log = logging.getLogger("configure")
log.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.basicConfig(filename="config.log", level=logging.DEBUG, format= "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
fh = logging.FileHandler("config.log", mode="w")
fh.setFormatter(formatter)
log.addHandler(fh)


NETBOX_URL = "http://192.168.100.61:8000/graphql/"
NETBOX_TOKEN = "nbt_h3qEhlejsjCk.ax8jNb7Z28tff9NBamuZmdF6JhvdXBOfTjzHZzI0"

query = """
query GetNornirDevices($limit: Int!, $offset: Int!) {
  device_list(
    filters: {
      platform: { name: { i_contains: "ios" } }
      primary_ip4: { assigned: true }
    }
    
    pagination: { limit: $limit, offset: $offset }
  ) {
    id
    name
    primary_ip4 {
      address
    }
    platform {
      slug
    }
    site {
      slug
    }
    interfaces {
      name
      ip_addresses {
        address
      }
    }
  }
}

"""

def fetch_netbox_devices(url: str, token: str, page_size: int = 100) -> list:
    headers = {
        "authorization": f"Token {token}",
        "Content-Type": "application/json",
    }

    devices = []
    offset = 0

    while True:
        response = requests.post(
            url,
            json={"query": query, "variables": {"limit": page_size, "offset": offset}},
            headers=headers,
            timeout=30,
        )

        response.raise_for_status()
        payload = response.json()
        if "errors" in payload:
            raise RuntimeError(f"GraphQL Error: {payload['errors']}")
        batch = payload["data"]["device_list"]
        devices.extend(batch)

        if len(batch) < page_size:
            break
        offset += page_size


    return devices

def build_nornir_hosts(devices: list) -> list:
    hosts = []
    for dev in devices:
        mgmt_ip = None

        # Loop through the device's list of interfaces
        for iface in dev.get("interfaces", []):
            if iface["name"] == "GigabitEthernet1/0/1" and iface.get("ip_addresses"):
                # Grab the first IP dictionary from the ip_addresses list
                raw_ip = iface["ip_addresses"][0]["address"]
                mgmt_ip = raw_ip.split("/")[0]
                break

        
        if not mgmt_ip:
            continue
        platform_slug = (dev.get("platform") or {}).get("slug", "")
        platform = "cisco_ios" if "ios" in platform_slug else platform_slug

        hosts.append({
            "name": dev["name"],
            "hostname": mgmt_ip,
            "platform": platform,
            "username": "yusufnje",
            "password": "*VPmL5y26&aM_$p",
            "netbox_id": dev["id"],
            "site": dev.get("site"),
            "interfaces": dev.get("interfaces", []),
            "port": 830,
            "connection_options": {
                "scrapli_netconf": {                                                         
                    "extras": {                     
                      "auth_strict_key": False,
                      "timeout_socket": 10.0,
                      "timeout_transport": 15.0,
                      "timeout_banner": 30,
                    },
                },
            },
        })
    print(hosts)
    return hosts

# checking candidate datastore
def _has_candidate(conn) -> bool:
    try:
        caps = conn.server_capabilities
    except AttributeError:
        try:
            caps = conn.get_capabilities()
        except Exception:
            return False
    if not caps:
        return False
    for cap in caps:
        if "candidate" in str(cap).lower():
            return True
    return False

# confirm commit
def _confirmed_commit(conn, timeout: int = 120):
    conn.send_rpc(
        payload=(
            '<rpc message-id="102" xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">'
            f'<commit><confirmed/><confirm-timeout>{timeout}</confirm-timeout></commit>'
            '</rpc>'
        ),
        operation="commit",
    )

#svi netconf
SVI_RPC_TEMPLATE = """
<config xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">
  <native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native">
    <interface>
      <Vlan>
        <name>10</name>
        <description>sharnam</description>
        <ip>
          <address>
            <primary>
              <address>192.168.239.239</address>
              <mask>24</mask>
            </primary>
          </address>
        </ip>
      </Vlan>
    </interface>
  </native>
</config>
"""


def config_svi(task: Task)-> Result:
    conn = task.host.get_connection("scrapli_netconf", task.nornir.config)
    has_candidate = _has_candidate(conn)
    if not has_candidate:
        return Result(
            host=task.host,
            result="device has no candidate",
            failed=True,
        )
    
    if has_candidate:
       resp = task.run(
         task=netconf_edit_config,
         config=SVI_RPC_TEMPLATE,
         target="candidate",
        )
       
       if resp.failed:
           return Result(
               host=task.host,
               result=f"device failed {resp.exception}",
               failed=True,
           )

       _confirmed_commit(conn, timeout = 120)

       return Result(
        host=task.host,
        result=f"SVI configured via _confirm_commit"
       )

    

raw_devices = fetch_netbox_devices(NETBOX_URL, NETBOX_TOKEN, page_size=50)
hosts_dict = build_nornir_hosts(raw_devices)

nr = InitNornir(
    runner={"plugin": "threaded", "options": {"num_workers": 20}},
    inventory={
        "plugin": "FlatDataInventory",
        "options": {
            "data": hosts_dict,
            
        },
    },
)

target_devices = nr.filter(platform="cisco_ios")
results = target_devices.run(task=config_svi)

#check for failures in Results returned by worker function
failed_hosts = [name for name, res in results.items() if res.failed]
ok_hosts     = [name for name, res in results.items() if not res.failed]

def confirm_commit(task: Task) -> Result:
    conn = task.host.get_connection("scrapli_netconf", task.nornir.config)
    conn.send_rpc(
        payload=(
            '<rpc message-id="103" xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">'
            '<commit/>'
            '</rpc>'
        ),
        operation="commit",
    )
    return Result(host=task.host, result="Confirmed commit")


def rollback(task: Task) -> Result:
    conn = task.host.get_connection("scrapli_netconf", task.nornir.config)
    conn.send_rpc(
        payload=(
            '<rpc message-id="104" xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">'
            '<cancel-commit/>'
            '</rpc>'
        ),
        operation="cancel-commit",
    )
    return Result(host=task.host, result="Rolled back")

def discard(task: Task) -> Result:
    conn = task.host.get_connection("scrapli_netconf", task.nornir.config)
    conn.send_rpc(payload="<discard-changes/>", operation="discard-changes")
    return Result(host=task.host, result="discarded candidate")

#if failed_hosts:
 #   nr.filter(lambda h: h.name in failed_hosts).run(task=rollback)

#if ok_hosts:
 #   nr.filter(lambda h: h.name in ok_hosts).run(task=confirm_commit)

if any(m.failed for m in results.values()):
    if ok_hosts:
        nr.filter(lambda h: h.name in ok_hosts).run(task=rollback)       # cancel-commit
    if failed_hosts:
        nr.filter(lambda h: h.name in failed_hosts).run(task=discard)    # discard candidate
else:
    # All good: commit on every host
    nr.filter(lambda h: h.name in ok_hosts).run(task=confirm_commit)

print_result(results)



# (for scalabiity and handling multiple groups, you can use the following approach(grouping by platform and role) to run tasks and handle commit/rollback logic. Uncomment and modify as needed.)
# --- 1. Group and Run ---
#ios_core_devices = nr.filter(platform="cisco_ios", role="core")
#nxos_access_devices = nr.filter(platform="cisco_nxos", role="access")

# Dictionary to hold all results for the end summary
#all_results = {}

# Run IOS Core
#if len(ios_core_devices.inventory.hosts) > 0:
 #   print(f"Running config_svi_ios_core on {len(ios_core_devices.inventory.hosts)} devices...")
  #  res_ios = ios_core_devices.run(task=config_svi_ios_core)
   # all_results.update(res_ios)

# Run NXOS Access
#if len(nxos_access_devices.inventory.hosts) > 0:
 #   print(f"Running config_svi_nxos_access on {len(nxos_access_devices.inventory.hosts)} devices...")
  #  res_nxos = nxos_access_devices.run(task=config_svi_nxos_access)
   # all_results.update(res_nxos)


# --- 2. Evaluate and Commit/Rollback ---
# Check if ANY device across ALL groups failed
#has_failures = any(multi.failed for multi in all_results.values())

#if has_failures:
 #   print("❌ Failure detected in at least one group. Initiating global rollback...")
    
    # Rollback only the groups that actually ran
  #  if len(ios_core_devices.inventory.hosts) > 0:
   #     ios_core_devices.run(task=rollback)
        
    #if len(nxos_access_devices.inventory.hosts) > 0:
     #   nxos_access_devices.run(task=rollback)
        
#else:
 #   print("✅ All groups staged successfully. Confirming commits...")
    
    # Confirm commits for the groups that ran
  #  if len(ios_core_devices.inventory.hosts) > 0:
   #     ios_core_devices.run(task=confirm_commit)
        
    #if len(nxos_access_devices.inventory.hosts) > 0:
     #   nxos_access_devices.run(task=confirm_commit)

# Print the results
#print_result(all_results)