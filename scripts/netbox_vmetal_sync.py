#!/usr/bin/env python3
import os
import sys
import argparse
import yaml
import pynetbox

def parse_args():
    parser = argparse.ArgumentParser(
        description="Sync NetBox Server Devices to vMetal BareMetalHosts in Kubernetes."
    )
    parser.add_argument(
        "--netbox-url",
        default=os.environ.get("NETBOX_URL", "http://localhost:8000"),
        help="NetBox URL (default: http://localhost:8000 or env NETBOX_URL)"
    )
    parser.add_argument(
        "--netbox-token",
        default=os.environ.get("NETBOX_TOKEN", ""),
        help="NetBox API Token (default: env NETBOX_TOKEN)"
    )
    parser.add_argument(
        "--namespace",
        default=os.environ.get("KUBERNETES_NAMESPACE", "vmetal-system"),
        help="Kubernetes target namespace (default: vmetal-system or env KUBERNETES_NAMESPACE)"
    )
    parser.add_argument(
        "--status",
        default="staged",
        help="Filter NetBox devices by status (default: staged)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the generated YAML manifests to stdout instead of applying them to the cluster."
    )
    parser.add_argument(
        "--bmc-scheme",
        default="redfish-virtualmedia",
        choices=["redfish-virtualmedia", "redfish", "ipmi"],
        help="BMC connection scheme/driver (default: redfish-virtualmedia)"
    )
    return parser.parse_args()

def get_netbox_client(url, token):
    if not token:
        print("Error: NetBox API Token is required. Set --netbox-token or env NETBOX_TOKEN.", file=sys.stderr)
        sys.exit(1)
    
    # Strip any Token prefixes
    cleaned_token = token.strip()
    for prefix in ("Token ", "Bearer "):
        if cleaned_token.lower().startswith(prefix.lower()):
            cleaned_token = cleaned_token[len(prefix):].strip()
            
    print(f"Connecting to NetBox at {url}...", file=sys.stderr)
    return pynetbox.api(url, token=cleaned_token)

def apply_to_kubernetes(secret_manifest, bmh_manifest, namespace):
    """Applies the manifests directly using the kubernetes python library."""
    try:
        from kubernetes import client, config
        from kubernetes.client.rest import ApiException
        
        # Load cluster or local kubeconfig
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
            
        core_api = client.CoreV1Api()
        custom_api = client.CustomObjectsApi()
        
        secret_name = secret_manifest["metadata"]["name"]
        bmh_name = bmh_manifest["metadata"]["name"]
        
        # 1. Apply Secret
        try:
            core_api.read_namespaced_secret(secret_name, namespace)
            core_api.replace_namespaced_secret(secret_name, namespace, secret_manifest)
            print(f"Secret '{secret_name}' updated in namespace '{namespace}'", file=sys.stderr)
        except ApiException as e:
            if e.status == 404:
                core_api.create_namespaced_secret(namespace, secret_manifest)
                print(f"Secret '{secret_name}' created in namespace '{namespace}'", file=sys.stderr)
            else:
                raise e
                
        # 2. Apply BareMetalHost
        group = "metal3.io"
        version = "v1alpha1"
        plural = "baremetalhosts"
        
        try:
            custom_api.get_namespaced_custom_object(group, version, namespace, plural, bmh_name)
            # Fetch current resourceVersion to allow update
            current = custom_api.get_namespaced_custom_object(group, version, namespace, plural, bmh_name)
            bmh_manifest["metadata"]["resourceVersion"] = current["metadata"]["resourceVersion"]
            custom_api.replace_namespaced_custom_object(group, version, namespace, plural, bmh_name, bmh_manifest)
            print(f"BareMetalHost '{bmh_name}' updated in namespace '{namespace}'", file=sys.stderr)
        except ApiException as e:
            if e.status == 404:
                custom_api.create_namespaced_custom_object(group, version, namespace, plural, bmh_manifest)
                print(f"BareMetalHost '{bmh_name}' created in namespace '{namespace}'", file=sys.stderr)
            else:
                raise e
                
    except ImportError:
        print("\n[WARNING] 'kubernetes' python library not found. Falling back to stdout output.", file=sys.stderr)
        print("Install it with: pip install kubernetes\n", file=sys.stderr)
        print_manifests(secret_manifest, bmh_manifest)
    except Exception as e:
        print(f"Error applying resources to Kubernetes: {e}", file=sys.stderr)
        sys.exit(1)

def print_manifests(secret, bmh):
    print("---")
    print(yaml.safe_dump(secret, default_flow_style=False))
    print("---")
    print(yaml.safe_dump(bmh, default_flow_style=False))

def main():
    args = parse_args()
    nb = get_netbox_client(args.netbox_url, args.netbox_token)
    
    try:
        # Query devices of role 'server' with the target status
        # Note: adjust role query filter if you use a slug or name
        devices = nb.dcim.devices.filter(role="server", status=args.status)
    except Exception as e:
        print(f"Failed to query NetBox devices: {e}", file=sys.stderr)
        sys.exit(1)
        
    device_list = list(devices)
    print(f"Found {len(device_list)} server devices with status '{args.status}'", file=sys.stderr)
    
    manifests_to_print = []
    
    for device in device_list:
        print(f"\nProcessing server: {device.name} (ID: {device.id})", file=sys.stderr)
        
        # 1. Resolve BMC details
        bmc_ip = None
        bmc_username = None
        bmc_password = None
        
        # Read from local context
        context = getattr(device, "local_context_data", {}) or {}
        if isinstance(context, dict) and "bmc" in context:
            bmc_ip = context["bmc"].get("ip")
            bmc_username = context["bmc"].get("username")
            bmc_password = context["bmc"].get("password")
            
        # Fallback to primary IP if BMC IP is not in local context
        if not bmc_ip and device.primary_ip4:
            bmc_ip = device.primary_ip4.address.split("/")[0]
            
        if not bmc_ip:
            print(f"  [Skip] No BMC IP found on device context or primary_ip4. Skipping.", file=sys.stderr)
            continue
            
        # 2. Resolve Boot MAC Address
        boot_mac = None
        interfaces = nb.dcim.interfaces.filter(device_id=device.id)
        for iface in interfaces:
            if iface.mac_address:
                # Prefer 'eth0' or 'mgmt' or take the first one with a MAC address
                if iface.name.lower() in ("eth0", "bmc", "mgmt", "ipmi"):
                    boot_mac = iface.mac_address.lower()
                    break
                if boot_mac is None:
                    boot_mac = iface.mac_address.lower()
                    
        if not boot_mac:
            print(f"  [Skip] No MAC address found on any interface. Skipping.", file=sys.stderr)
            continue
            
        print(f"  Resolved BMC IP: {bmc_ip}", file=sys.stderr)
        print(f"  Resolved Boot MAC: {boot_mac}", file=sys.stderr)
        
        # 3. Build manifests
        secret_name = f"{device.name.lower()}-bmc-secret"
        secret_manifest = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": secret_name,
                "namespace": args.namespace,
                "labels": {
                    "app.kubernetes.io/managed-by": "netbox-vmetal-sync",
                    "netbox-device-id": str(device.id),
                }
            },
            "type": "Opaque",
            "stringData": {
                "username": bmc_username or "admin",
                "password": bmc_password or "admin",
            }
        }
        
        # Format BMC Address URL based on scheme
        if args.bmc_scheme.startswith("redfish"):
            bmc_address = f"{args.bmc_scheme}://{bmc_ip}/redfish/v1/Systems/System.Embedded.1"
        else:
            bmc_address = f"{args.bmc_scheme}://{bmc_ip}"
            
        bmh_manifest = {
            "apiVersion": "metal3.io/v1alpha1",
            "kind": "BareMetalHost",
            "metadata": {
                "name": device.name.lower(),
                "namespace": args.namespace,
                "labels": {
                    "app.kubernetes.io/managed-by": "netbox-vmetal-sync",
                    "netbox-device-id": str(device.id),
                    "netbox-device-status": args.status,
                }
            },
            "spec": {
                "bmc": {
                    "address": bmc_address,
                    "credentialsName": secret_name,
                    "disableCertificateVerification": True,
                },
                "bootMACAddress": boot_mac,
                "online": True,
            }
        }
        
        if args.dry_run:
            manifests_to_print.append((secret_manifest, bmh_manifest))
        else:
            apply_to_kubernetes(secret_manifest, bmh_manifest, args.namespace)
            
    if args.dry_run and manifests_to_print:
        print("\n--- DRY RUN OUTPUT ---", file=sys.stderr)
        for secret, bmh in manifests_to_print:
            print_manifests(secret, bmh)

if __name__ == "__main__":
    main()
