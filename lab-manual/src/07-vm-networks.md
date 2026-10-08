# Chapter 7 · VM Networking: NMState, Bridges and VLANs

Pods live on a private overlay network (the `clusterNetwork` 10.128.0.0/14 from Chapter 3) and reach the outside through NAT and Routes. A migrated server VM usually needs something different: **its own IP on the same VLAN it had in VMware**, so clients, firewalls and DNS keep working. That needs three layers, and each one maps to something you already build in vSphere.

| LAYER | OPENSHIFT OBJECT | VSPHERE EQUIVALENT | IN THIS LAB |
|---|---|---|---|
| Physical uplink | Node NIC `ens34` | vmnic on the ESXi host | NIC2 of each node VM, on a trunk port group |
| Switch | Linux bridge `br-vm`, created by a **NodeNetworkConfigurationPolicy (NNCP)** | Standard / distributed vSwitch | VLAN-aware, trunk 2 to 4094 |
| VM network | **NetworkAttachmentDefinition (NAD)** | Port group with a VLAN ID | `vlan-110`, `vlan-120`, `vlan-130` |

```text
  VM (net-0) ─► NAD default/vlan-110 (VLAN 110) ─► br-vm (linux bridge, trunk) ─► ens34 ─► vSphere trunk PG (VLAN 4095) ─► physical switch
```

> **NOTE** Because the OpenShift nodes are themselves VMware VMs, NIC2 sits on a vSphere port group with **VLAN 4095** (VGT, "pass all VLAN tags to the guest"), and that port group must allow *Promiscuous mode*, *MAC address changes* and *Forged transmits*. Without that, ESXi drops every frame from a nested VM, because its MAC is not the node VM's MAC. On bare metal this whole note disappears: `ens34` is a real NIC on a real trunk.

```powershell
# Reference: New-OcpVmTrunk.ps1 (jump host) creates the trunk port group and moves NIC2 of OCP-NODE-1/2/3 onto it
PS C:\> .\New-OcpVmTrunk.ps1 -VCenter vcenter.example.com
```

## Layer 2: the bridge (NMState)

The **Kubernetes NMState Operator** lets you configure node networking (bonds, VLANs, bridges, IPs, routes, DNS) through the API instead of editing nodes. You declare the *desired state*; a handler Pod on each node applies it, tests it, and **rolls it back automatically** if the node loses connectivity to the API.

```yaml
# Reference: ~/ocp-install/vm-networks.sh, part 1
apiVersion: nmstate.io/v1
kind: NodeNetworkConfigurationPolicy
metadata: {name: br-vm-ens34}
spec:
  nodeSelector: {node-role.kubernetes.io/worker: ""}
  desiredState:
    interfaces:
    - name: br-vm
      type: linux-bridge
      state: up
      ipv4: {enabled: false}             # the bridge carries VM traffic only; the node has no IP on it
      ipv6: {enabled: false}
      bridge:
        options: {stp: {enabled: false}}
        port:
        - name: ens34
          vlan:
            mode: trunk
            trunk-tags:
            - id-range: {min: 2, max: 4094}
```

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get nncp; oc get nnce
NAME          STATUS      REASON
br-vm-ens34   Available   SuccessfullyConfigured
NAME                    STATUS      STATUS AGE   REASON
ocp-node-1.br-vm-ens34   Available   8h           SuccessfullyConfigured
ocp-node-2.br-vm-ens34   Available   8h           SuccessfullyConfigured
ocp-node-3.br-vm-ens34   Available   8h           SuccessfullyConfigured
```

The **policy** (NNCP) is the cluster-wide wish; there is one **enactment** (NNCE) per node it applied to, each with its own status. If one node fails, its NNCE says `Failing` with the reason, and the others are untouched.

> **TIP** `nodeSelector: node-role.kubernetes.io/worker: ""` matched all three nodes because on a compact cluster every node carries the `worker` role label. On a cluster with separate workers, the same policy would deliberately skip the control plane.

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get nns ocp-node-1 -o jsonpath="{range .status.currentState.interfaces[*]}{.name}{\"\t\"}{.type}{\"\t\"}{.state}{\"\n\"}{end}" | grep -vE "veth|genev|ovs-interface" | head -20
br-ex	ovs-bridge	up
br-vm	linux-bridge	up
ens33	ethernet	up
ens34	ethernet	up
lo	loopback	up
```

The **NodeNetworkState** (NNS) is NMState's live report of each node. You can read it here, without logging in to the node:

- `ens33` is NIC1. Its IP (.34) lives on `br-ex`, the Open vSwitch bridge OVN-Kubernetes builds for the cluster network. That is why `ens33` shows no IP on the node console: normal, not a fault.
- `ens34` is NIC2, enslaved to `br-vm`, our linux bridge.

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc debug node/ocp-node-1 --to-namespace=default -q -- chroot /host bash -c "ip -br link show master br-vm; bridge vlan show dev ens34 | head -4"
ens34            UP             00:50:56:aa:00:12 <BROADCAST,MULTICAST,UP,LOWER_UP> 
port              vlan-id  
ens34             1 PVID Egress Untagged
                  2
                  3
```

From inside the node: `ens34` (MAC `00:50:56:aa:00:12`, the same MAC the agent config called `ens224` in Chapter 3) is the only port of `br-vm`, and it accepts VLANs 2, 3 and onward (trimmed with `head`; the list runs to 4094). VLAN 1 is the untagged native VLAN.

## Layer 3: the VM networks (NADs)

```yaml
# Reference: ~/ocp-install/vm-networks.sh, part 2 (one of three)
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata:
  name: vlan-110
  namespace: default
  annotations: {k8s.v1.cni.cncf.io/resourceName: bridge.network.kubevirt.io/br-vm}
spec:
  config: '{"cniVersion":"0.3.1","name":"vlan-110","type":"bridge","bridge":"br-vm","vlan":110,"macspoofchk":true,"ipam":{}}'
```

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get net-attach-def -A
NAMESPACE                  NAME         AGE
default                    vlan-120   8h
default                    vlan-130   8h
default                    vlan-110    8h
openshift-ovn-kubernetes   default      13h
```

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get net-attach-def vlan-110 -n default -o jsonpath="{.spec.config}" | python3 -m json.tool
{
    "cniVersion": "0.3.1",
    "name": "vlan-110",
    "type": "bridge",
    "bridge": "br-vm",
    "vlan": 110,
    "macspoofchk": true,
    "ipam": {}
}
```

| KEY | MEANING |
|---|---|
| `"type": "bridge"` | Use the linux-bridge CNI plug-in |
| `"bridge": "br-vm"` | Plug into the bridge the NNCP created |
| `"vlan": 110` | Tag this VM's traffic with VLAN 110: the "VLAN ID" field of a port group |
| `"macspoofchk": true` | Drop frames whose source MAC is not the VM's own (like *Forged transmits: Reject*) |
| `"ipam": {}` | OpenShift gives the VM no IP; the guest uses DHCP or its static IP, as on VMware |
| `resourceName` annotation | Only schedule VMs on nodes that actually have `br-vm` |

> **NOTE** NADs in the `default` namespace can be used by VMs in **any** project (reference it as `default/vlan-110`). A NAD in a project is visible only to that project. Put shared, admin-controlled networks in `default` (or a dedicated namespace) and keep project NADs for project-specific needs.

> **SCREENSHOT S-06:** *Networking > NodeNetworkConfigurationPolicy*: `br-vm-ens34` Available on 3 nodes, with the topology view showing `ens34` under `br-vm`.

> **SCREENSHOT S-07:** *Networking > NetworkAttachmentDefinitions*, project `default`: `vlan-110`, `vlan-120`, `vlan-130`, type *Linux bridge*.

> **GOTCHA — REAL ISSUE** Applying the NNCP is a **cluster networking change** on every node. NMState will roll it back if a node loses its API connection, but a mistake on the wrong NIC (for example `ens33`, which carries the node IP) can still take a node off the network for the rollback timeout. Always name the spare NIC, check `oc get nns <node>` first, and apply to one node (`nodeSelector: kubernetes.io/hostname: ocp-node-1`) before all.

> **GOTCHA — REAL ISSUE** The VLAN ID 110 for the old network was *assumed* from the port-group name, not confirmed with the network team. If a migrated VM on `vlan-110` gets no traffic, compare the real VLAN ID on the vSphere port group (*Edit settings > VLAN*) with the NAD's `"vlan"` value first.

> **PRODUCTION** Use a **bond** of two NICs under the bridge (NNCP `type: bond`, `mode: 802.3ad` or `active-backup`), one NAD per production VLAN, and consider OVN-Kubernetes **localnet** secondary networks (`type: ovn-k8s-cni-overlay`, `topology: localnet`) if you want network policy on VM traffic. Keep live-migration traffic on its own network (HCO `liveMigrationConfig.network`).

> **EXAM TIP** EX316: create an NNCP for a linux bridge, create a NAD (YAML and console), attach a VM to it as a second NIC, and explain pod network (masquerade) vs bridge (L2) binding.

> **LAB — DO IT ON THE BASTION** (1) Read the NNS of all three nodes and find the MAC of `ens34` on each. Compare with `agent-config.yaml`. (2) Add a second NIC on `default/vlan-120` to `rhel9-demo` from Chapter 6 (console: *Configuration > Network > Add network interface*), boot it, and run `ip a` in the guest. (3) Explain why it gets no IP if VLAN 120 has no DHCP server.

## Check yourself

**Q1. A NAD says `"vlan": 110`. Where does the VLAN tag get added: in the guest, on `br-vm`, or on the physical switch?**
On `br-vm`, at the VM's bridge port. *Logic:* the guest sends untagged frames (like a VM on a port group with VLAN 110); the bridge tags them and the trunk on `ens34` carries them out tagged.

**Q2. The NNCP shows `Available` but a VM on `vlan-110` cannot reach its gateway. In this nested lab, what two vSphere settings do you check?**
That NIC2 is on the trunk port group (VLAN 4095) and that the port group allows promiscuous mode, MAC changes and forged transmits. *Logic:* the bridge is fine (NNCE says so); frames are being dropped outside OpenShift, by the ESXi vSwitch.

**Q3. Why does `ens33` have no IP address when you look at the node console?**
OVN-Kubernetes moves the node IP onto the OVS bridge `br-ex`, with `ens33` as its port. *Logic:* the bridge must own the IP so it can switch Pod and node traffic on the same uplink.

**Q4. What is the difference between an NNCP and a NAD?**
The NNCP configures the **node** (creates `br-vm` on `ens34`); the NAD defines a **network VMs can attach to** (VLAN 110 on `br-vm`). *Logic:* one is the vSwitch, the other is the port group.
