# Part A: Foundations

## 1. The big picture: what a migration actually does

**Concept.** A VMware VM is three things: a definition (CPU, RAM, NICs), one or more disk files (VMDKs on a datastore), and a running process on an ESXi host. Migrating it to OpenShift means:

1. **Copy the disks** from the datastore into OpenShift storage (each VMDK becomes a PVC).
2. **Convert the guest** so it boots on KVM instead of ESXi: remove VMware Tools, inject VirtIO drivers, fix the boot loader. This is done by `virt-v2v`, a Red Hat tool MTV runs for you.
3. **Create the VM definition** in OpenShift with the same CPU, RAM, MAC addresses and NICs, plugged into the mapped networks.
4. **Start it** on an OpenShift node.

MTV (Migration Toolkit for Virtualization) automates all four. You describe the move with a few objects (Provider, NetworkMap, StorageMap, Plan, Migration) and MTV does the rest.

```text
 VMware side                               OpenShift side (cluster ocp1)
 ------------                              ----------------------------------------
 vCenter  <---- API (443) ---- MTV inventory (reads VMs, networks, datastores)
   |
 ESXi hosts <-- disk read (443/902, VDDK) -- MTV disk transfer pod
   |                                              |
 Datastore (VMDK)                                 v
                                   PVC on StorageClass (nfs-csi in lab;
                                   FC / iSCSI / NFS array CSI in production)
                                                  |
                                   virt-v2v conversion pod (drivers, bootloader)
                                                  |
                                   VirtualMachine object -> virt-launcher pod on a node
                                                  |
 Port group 110 (VLAN 110) <== same VLAN ==> NAD vlan-110 -> br-vm -> ens34
```

**Why this matters:** each arrow is a dependency. If a migration fails, find which arrow broke: vCenter API (provider not Ready), disk read (ports 443/902, VDDK), storage (PVC Pending), conversion (virt-v2v logs), or network (VM boots but no IP).

### Cold vs warm migration

| | Cold | Warm |
|---|---|---|
| Source VM during copy | Powered off | Keeps running |
| How data is copied | Full copy once | Full copy, then repeated delta copies using CBT, then a final delta at cutover |
| Downtime | Full copy + conversion | Final delta + conversion + boot (minutes) |
| Needs | Nothing extra | CBT enabled on the VM, VDDK image strongly recommended |
| Use for | Test VMs, small VMs, VMs that can be off for hours | Production VMs with a short change window |

> **SCREENSHOT S-01:** none needed here. Optional: a slide of the diagram above for presentations.

### Check yourself

**Q1. A VM migrated, booted, but has no network. Which part of the pipeline do you look at first?**
Answer: the network mapping and the VLAN path (NetworkMap target, NAD VLAN ID, bridge on the node, vSphere trunk port group). Logic: disk copy and conversion succeeded because the VM booted, so the failure is after boot, which is the network path.

**Q2. Why does MTV need to "convert" a VM at all? Linux runs on any x86 server.**
Answer: the OS runs anywhere, but its drivers and boot configuration do not. A VMware VM uses VMware paravirtual devices (PVSCSI, VMXNET3) and VMware Tools. On KVM the disks and NICs are VirtIO devices. Without VirtIO drivers in the initramfs (Linux) or the driver store (Windows) the guest cannot see its boot disk and crashes at boot.

## 2. Architecture: lab and production

### 2.1 Lab

```text
                   +--------------------- vSphere cluster (DC1) ----------------------+
                   |                                                                  |
  Bastion .24  ----+--- OCP-NODE-1 (.34)   OCP-NODE-2 (.35)   OCP-NODE-3 (.36)            |
  RHEL 10          |    12 vCPU/48 GB     12 vCPU/48 GB      12 vCPU/48 GB             |
  oc, NFS export   |    NIC1 ens33 -> PG-OCP-NODES (node IP, 198.51.100.32/27)            |
  /exports/ocp     |    NIC2 ens34 -> PG-VM-TRUNK (VLAN 4095 = all VLANs) -> br-vm    |
                   |    Nested hardware virtualization exposed to the guest            |
                   +------------------------------------------------------------------+
  API VIP .37, Ingress VIP .38; DNS on AD (203.0.113.251/.252); NTP 203.0.113.117/.118
```

The lab runs OpenShift **inside** VMware VMs (nested virtualization). That is fine for learning and for proving the process. Production runs OpenShift on bare-metal servers.

### 2.2 Production reference architecture

```text
  +-------------------------- OpenShift cluster (bare metal) ---------------------------+
  | 3 control-plane nodes (can also run VMs in a compact cluster)                       |
  | N worker nodes (VM hosts): 2x NIC bonds                                             |
  |    bond0 (2x 25 GbE) -> br-ex: node, API, pod traffic, live migration (or separate)  |
  |    bond1 (2x 25 GbE) -> br-vm / OVS bridge: VLAN trunk for VM networks              |
  |    FC HBAs (2 ports, 2 fabrics)  -> FC array LUNs via the vendor CSI driver          |
  |    or iSCSI NICs (2 subnets)     -> iSCSI array via the vendor CSI driver           |
  |    NFS                           -> NFS array via the vendor CSI driver             |
  +--------------------------------------------------------------------------------------+
         ^ 443 to vCenter, 443 + 902 to every ESXi host (MTV disk reads)
  vCenter + ESXi (source)
```

**PRODUCTION:** sizing rule of thumb for VM-hosting workers: add up the vCPU and RAM of the VMs you will move, allow for overcommit only where you allowed it on VMware, then add capacity for one node to fail (N+1) and for live-migration headroom. OpenShift Virtualization reserves some memory per VM for overhead (roughly 200 to 300 MB per VM plus a little per vCPU), so a host full of small VMs needs more RAM than the VM total suggests.

## 3. Linux basics you need for this SOP

You will run every CLI step on the **bastion**, a RHEL Linux server. You only need a handful of commands.

| Task | Command | VMware analogy |
|---|---|---|
| Connect to the bastion | `ssh admin@198.51.100.24` (from PuTTY, Windows Terminal or MobaXterm) | Opening an SSH session to an ESXi host |
| Where am I? | `pwd` | |
| List files | `ls -l` | Browsing a datastore |
| Read a file | `cat file`, `less file` (q to quit) | |
| Edit a file | `vi file` (i to insert, Esc then `:wq` to save) or `nano file` | |
| Run as administrator | `sudo <command>` | Using root on ESXi |
| See IP addresses | `ip -br addr` | vmk adapters |
| See routes | `ip route` | |
| Test name resolution | `dig +short <name>` | `nslookup` |
| Test a port | `curl -kv https://<host>:443` or `nc -zv <host> 902` | |

Real output from the lab bastion:

```text
$ cat /etc/redhat-release; uname -r
Red Hat Enterprise Linux release 10.0 (Coughlan)
6.12.0-55.9.1.el10_0.x86_64

$ ip -br addr
lo               UNKNOWN        127.0.0.1/8 ::1/128
ens33            UP             198.51.100.24/24 fe80::250:56ff:fe00:1/64

$ ip route
default via 198.51.100.1 dev ens33 proto static metric 100
198.51.100.0/24 dev ens33 proto kernel scope link src 198.51.100.24 metric 100
198.51.100.32/27 via 198.51.100.1 dev ens33 proto static metric 100
198.51.100.64/27 via 198.51.100.1 dev ens33 proto static metric 100
198.51.100.96/27 via 198.51.100.1 dev ens33 proto static metric 100
```

**How to read it:** the bastion has one NIC `ens33` with address 198.51.100.24 and a /24 mask. Because the mask is /24, it believes the whole 198.51.100.x range is directly connected. The OpenShift nodes live in a separate VLAN (198.51.100.32/27), so three static routes send that traffic through the gateway 198.51.100.1 instead. Without those routes the bastion could not reach the cluster.

```text
$ dig +short api.ocp1.example.com
198.51.100.37
$ dig +short test.apps.ocp1.example.com
198.51.100.38
$ dig +short vcenter.example.com
203.0.113.9
```

**How to read it:** the API name resolves to the API VIP, any name under `*.apps` resolves to the ingress VIP (that is how the web console and MTV routes work), and vCenter resolves. All three must work before anything else in this SOP will.

## 4. Networking basics you need

| Term | Meaning | Example in the lab |
|---|---|---|
| IP address | A server's address on a network | 198.51.100.34 |
| Subnet / prefix | Which addresses are "local". /27 = 32 addresses | 198.51.100.32/27 = .32 to .63 |
| Gateway | The router used to reach other subnets | 198.51.100.33 |
| VLAN | A tag that splits one physical network into many logical ones. A port group in vSphere has a VLAN ID | 110, 100, 120, 130 |
| Trunk | A link that carries many VLANs, each frame tagged | vSphere port group with VLAN 4095, bridge `br-vm` trunk 2 to 4094 |
| Bridge | A software switch inside a host | `br-vm` on each node |
| DNS | Turns names into IPs | AD DNS 203.0.113.251 |
| VIP | Virtual IP that moves between nodes | API .37, apps .38 |

**Why VLAN trunks matter for VM migration:** on VMware, a VM's NIC sits on a port group with a VLAN ID. To keep the same IP after migration, the VM must land on the **same VLAN** on OpenShift. The OpenShift node therefore needs a NIC that carries that VLAN (a trunk) and a bridge the VM can plug into, with the VLAN ID set on the NAD.

## 5. `oc` basics

`oc` is the OpenShift command line. It talks to the cluster API at `https://api.<cluster>.<domain>:6443`.

### 5.1 Log in

**GUI:** open the web console, click your user name (top right) > **Copy login command** > **Display Token**, and copy the `oc login --token=...` line.

> **SCREENSHOT S-02:** Web console top-right user menu with "Copy login command" highlighted.

**CLI:**

```text
$ oc login -u ocpadmin https://api.ocp1.example.com:6443
$ oc whoami
ocpadmin
$ oc whoami --show-console
https://console-openshift-console.apps.ocp1.example.com
```

> In the lab the bastion can also use the installer's `system:admin` kubeconfig (`export KUBECONFIG=~/ocp1/auth/kubeconfig`). Keep that file locked away; in production it is break-glass only.

### 5.2 The six verbs you will use 90% of the time

| Command | What it does |
|---|---|
| `oc get <kind>` | List objects (`oc get vm`, `oc get pvc`, `oc get nodes`) |
| `oc get <kind> <name> -o yaml` | Show the full definition and status |
| `oc describe <kind> <name>` | Human-readable detail plus **Events** at the bottom (first place to look when something is stuck) |
| `oc apply -f file.yaml` | Create or update objects from a file |
| `oc logs <pod>` | Read a pod's log |
| `oc project <name>` / `-n <name>` | Choose the project (namespace) |

### 5.3 Reading YAML in 60 seconds

Every OpenShift object has the same shape:

```yaml
apiVersion: forklift.konveyor.io/v1beta1   # which API group and version
kind: Plan                                  # what type of object
metadata:
  name: test-ubuntu                         # its name
  namespace: openshift-mtv                  # which project it lives in
spec:                                       # what YOU want (desired state)
  targetNamespace: mtv-test
status:                                     # what the cluster reports (actual state)
  conditions:
  - type: Ready
    status: "True"
```

**Why:** OpenShift is declarative. You write the `spec`, controllers work until reality matches it, and they report progress in `status`. MTV is exactly this: you write a Plan, MTV's controller does the migration and fills in `status`.

Indentation is two spaces and it matters; tabs are not allowed.

### Check yourself

**Q1. `oc get plan test-ubuntu` shows READY True but nothing is happening. Why?**
Answer: a Plan is only the description. Nothing runs until you create a **Migration** object that references it (the GUI **Start** button creates one). Logic: spec vs action; MTV separates "what to move" from "move it now" so you can run the same plan again.

**Q2. Which command do you run first when an object is stuck and why?**
Answer: `oc describe <kind> <name>` and read the Events at the bottom, then `oc get <kind> <name> -o yaml` and read `status.conditions`. Logic: controllers write the reason they are stuck into events and conditions.
