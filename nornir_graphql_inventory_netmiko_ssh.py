import requests
from nornir import InitNornir
from nornir.core.task import Task, Result
from nornir_netmiko.tasks import netmiko_send_command
from nornir_utils.plugins.functions import print_result
from nornir import InitNornir
from ns_nornir_table_inventory.plugins.inventory import FlatDataInventory
from nornir.core.plugins.inventory import InventoryPluginRegister

InventoryPluginRegister.register("FlatDataInventory", FlatDataInventory)

from nornir_scrapli.tasks import netconf_edit_config, netconf_get_config



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
    
        })
    print(hosts)
    return hosts

def check_interfaces(task: Task) -> Result:
    netbox_ifaces = [iface["name"] for iface in task.host["interfaces"]]
    output = task.run(
        task=netmiko_send_command,
        command_string="show ip int brief",
    )

    return Result(
        host=task.host,
        result=f"netbox interfaces: {netbox_ifaces}\n\nDevice output:\n{output.result}",
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
results = target_devices.run(task=check_interfaces)
print_result(results)
