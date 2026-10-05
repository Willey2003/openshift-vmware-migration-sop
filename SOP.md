---
title: "SOP: Migrating Virtual Machines from VMware vSphere to Red Hat OpenShift Virtualization"
subtitle: "Beginner to advanced, web console (GUI) and command line (oc), lab-validated"
author: "Gaganpreet Singh"
date: "5 October 2026"
docid: "SOP-OCPV-MIG-001"
pagetitle: "VMware to OpenShift Virtualization SOP"
---

# Document control

| Field | Value |
|---|---|
| Document ID | SOP-OCPV-MIG-001 |
| Title | Migrating virtual machines from VMware vSphere to Red Hat OpenShift Virtualization |
| Version | 1.0 (draft for team review) |
| Owner | Gaganpreet Singh |
| Validated on | Lab cluster `ocp1`, 5 October 2026 |
| Product versions | OpenShift Container Platform 4.22.15 (Kubernetes 1.35.6), OpenShift Virtualization 4.22.9, Migration Toolkit for Virtualization (MTV) 2.12.9, Kubernetes NMState Operator 4.22, RHEL 10.0 bastion |
| Source platform | VMware vSphere 8 (vCenter `vcenter.example.com`, 4 ESXi hosts) |
| Review cycle | Every OpenShift minor release, or after any production wave |

## Revision history

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0 | 2026-10-05 | Gaganpreet Singh | First issue, written against the lab build |

## Approvals

| Role | Name | Signature | Date |
|---|---|---|---|
| Author | Gaganpreet Singh | | |
| Technical reviewer (Virtualization) | | | |
| Storage team | | | |
| Network team | | | |
| Change manager | | | |

# How to use this SOP

> **Placeholders.** Every hostname, IP address, VLAN, datastore and port group in this SOP is a placeholder taken from the documentation ranges (`example.com`, `192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`). The command output is real output from a lab build with those values substituted. Replace them with your own environment's values before running anything.

## Who it is for

This SOP is written for an infrastructure engineer who knows VMware vSphere well but is new to Linux, networking and Kubernetes. Every concept starts at the most basic level ("what is this and why does it exist"), then shows how to do it in the **web console (GUI)** and with the **command line (`oc`)**, then shows the **real output** captured from the lab, and finally explains how to read that output.

It is also written to be used as-is for a **production** migration. Where production differs from the lab (mostly storage: FC, iSCSI and NFS arrays instead of a single NFS export), the difference is called out in a box labelled **PRODUCTION**.

## Conventions

| You see | It means |
|---|---|
| `$ oc get nodes` | A command you type on the bastion (Linux jump host). Do not type the `$`. |
| Text in a grey block under a command | The real output from the lab on 5 October 2026. Your names, ages and IDs will differ. |
| `<ANGLE_BRACKETS>` | A value you must replace with your own (for example `<VCENTER_FQDN>`). |
| **GUI:** | Steps in the OpenShift web console or the vSphere Client. |
| **CLI:** | Steps with `oc` on the bastion. |
| **Why:** | The logic behind the step. Read it once; it is what makes the step stick. |
| **PRODUCTION:** | What changes in a production environment. |
| **SCREENSHOT S-nn** | Capture this screen and save it as `images/S-nn.png` next to this document. The build script places it automatically. |
| **Check yourself** | Review questions. Each answer explains its reasoning. |

## How each section is laid out

1. **Concept**: what it is, in plain language, with the VMware equivalent.
2. **GUI**: click path.
3. **CLI**: commands.
4. **Output**: real lab output and how to read it.
5. **Verify**: how you know it worked.
6. **Check yourself**: questions and answers with logic.

# Glossary: VMware terms mapped to OpenShift

If you remember one table from this document, make it this one.

| VMware vSphere | OpenShift / OpenShift Virtualization | Plain-language meaning |
|---|---|---|
| vCenter Server | OpenShift cluster API + web console | The place you manage everything from |
| ESXi host | Node (RHCOS server running KVM) | The physical or virtual server that runs VMs |
| Cluster (DRS/HA) | OpenShift cluster + scheduler | Decides which node runs a VM and restarts it if a node dies |
| VM | `VirtualMachine` (VM) object | The definition of the VM (CPU, RAM, disks, NICs) |
| Powered-on VM process | `VirtualMachineInstance` (VMI) + `virt-launcher` pod | The running copy of the VM |
| vMotion | Live migration | Move a running VM to another node with no downtime |
| Datastore | `StorageClass` | A type of storage you can create disks from |
| VMDK | `PersistentVolumeClaim` (PVC) / `DataVolume` | A VM disk |
| Thin/thick provisioning | StorageClass parameters, volume mode | How the disk is allocated |
| Port group (VLAN) | `NetworkAttachmentDefinition` (NAD) | A network a VM NIC can plug into |
| vSwitch / VDS uplink | Linux bridge or OVS bridge created by `NodeNetworkConfigurationPolicy` (NNCP) | The switch inside each host that VMs plug into |
| Resource pool / folder | Project (namespace) | A container for VMs with its own quotas and permissions |
| vCenter roles and permissions | RBAC (Roles, RoleBindings) | Who can do what |
| Templates / content library | Templates, `DataSource`, boot images | Golden images to create VMs from |
| VMware Tools | QEMU guest agent + VirtIO drivers | In-guest helpers for clean shutdown, IP reporting and fast drivers |
| Snapshot | `VirtualMachineSnapshot` | Point-in-time copy of a VM |
| VMware HCX / Converter | Migration Toolkit for Virtualization (MTV, upstream name Forklift) | The tool that moves VMs into OpenShift |
| Changed Block Tracking (CBT) | Used by MTV warm migration | Lets MTV copy only changed blocks |
| VDDK | VDDK init image used by MTV | VMware's fast disk-read library |

# Lab environment used in this SOP

| Item | Value |
|---|---|
| OpenShift cluster | `ocp1`, base domain `example.com` |
| Nodes | 3 combined control-plane and worker nodes `ocp-node-1/2/3`, 198.51.100.34, .35, .36 (subnet 198.51.100.32/27, gateway .33) |
| API VIP | `api.ocp1.example.com` = 198.51.100.37 |
| Ingress VIP | `*.apps.ocp1.example.com` = 198.51.100.38 |
| Web console | https://console-openshift-console.apps.ocp1.example.com |
| Node NICs | `ens33` = node IP (on OVS bridge `br-ex`), `ens34` = VM traffic (Linux bridge `br-vm`, VLAN trunk) |
| Bastion | `bastion.example.com`, 198.51.100.24, RHEL 10.0, user `admin`, `oc` in `~/bin` |
| Storage | NFS export `/exports/ocp` on the bastion (500 GB disk), CSI driver `nfs.csi.k8s.io`, StorageClass `nfs-csi` (default) |
| VM networks | NADs `vlan-110`, `vlan-120`, `vlan-130` in project `default` |
| vCenter | `vcenter.example.com` (203.0.113.9), MTV service account `mtv@vsphere.local` |
| DNS / NTP | AD DNS 203.0.113.251, .252; NTP 203.0.113.117, .118 |
| MTV objects | Provider `vcenter-lab`, NetworkMap `vcenter-lab-net`, StorageMap `vcenter-lab-storage`, Plan `test-ubuntu` |
| Test VM | `ubuntu-noble-24.04-cloudimg` (2 vCPU, 1 GB, 10 GB disk on datastore `DATASTORE-01`, port group `VM-Network-110`) |

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

# Part B: Build and verify the platform

## 6. Prerequisites checklist

Complete and sign this table before any migration work starts. Every row has caused a failed migration somewhere.

| # | Check | How to verify | Lab result |
|---|---|---|---|
| P1 | OpenShift cluster installed and healthy | `oc get clusterversion`, `oc get co` (section 7) | 4.22.15, all operators Available |
| P2 | Hardware virtualization on every node that will run VMs | `oc debug node/<node> -- chroot /host ls -l /dev/kvm` | Present (nested virtualization enabled on the vSphere VMs) |
| P3 | DNS: `api`, `api-int`, `*.apps` records | `dig +short api.<cluster>.<domain>` | .37 / .38 |
| P4 | Cluster can resolve vCenter and every ESXi host by name | `dig +short <vcenter>` from the bastion and from a node | 203.0.113.9 |
| P5 | NTP reachable by nodes | `oc debug node/<node> -- chroot /host chronyc sources` | 203.0.113.117/.118 |
| P6 | Firewall: nodes to vCenter TCP 443 | `curl -kI https://<vcenter>/sdk` | Open |
| P7 | Firewall: nodes to every ESXi host TCP 443 and 902 | `nc -zv <esxi> 902` | Open on esxi01 to esxi04 |
| P8 | Storage class for VM disks, ideally RWX | `oc get sc`, `oc get storageprofile` (section 8) | `nfs-csi`, RWX Filesystem |
| P9 | VM networks: node NIC on a trunk, bridge, NADs | `oc get nncp`, `oc get net-attach-def -A` (section 9) | `br-vm-ens34`, 3 NADs |
| P10 | vCenter service account with MTV role | Section 11 | `mtv@vsphere.local`, role `MTV-Migration` |
| P11 | VDDK image built and pushed | Section 12 | Optional in lab; required for warm in production |
| P12 | Source VMs prepared (no snapshots, CBT for warm, tools running) | Section 13 | Test VM ready |
| P13 | Change ticket, backups of source VMs, rollback agreed | Section 18 | n/a in lab |

**PRODUCTION:** add P14 "storage team has presented LUNs or exports and installed or approved the vendor CSI driver", and P15 "multipath configured on nodes" for FC and iSCSI. See section 17.

## 7. Cluster health checks

Run these before every migration wave. A degraded cluster turns a simple migration into an outage.

**GUI:** **Home > Overview**. The Status card should show Cluster, Control Plane, Operators and Dynamic Plugins green. Then **Administration > Cluster Settings** shows the version and update channel.

> **SCREENSHOT S-03:** Home > Overview with the Status card all green and the cluster inventory card.
>
> **SCREENSHOT S-04:** Administration > Cluster Settings > Details showing version 4.22.15.

**CLI:**

```text
$ oc version
Client Version: 4.22.15
Kustomize Version: v5.7.1
Server Version: 4.22.15
Kubernetes Version: v1.35.6

$ oc get clusterversion
NAME      VERSION   AVAILABLE   PROGRESSING   SINCE   STATUS
version   4.22.15   True        False         7h43m   Cluster version is 4.22.15
```

**How to read it:** `AVAILABLE True` and `PROGRESSING False` means the cluster is on the stated version and not mid-upgrade. Never start a migration wave while PROGRESSING is True.

```text
$ oc get nodes -o wide
NAME        STATUS   ROLES                         AGE     VERSION   INTERNAL-IP   OS-IMAGE                                                KERNEL-VERSION
ocp-node-1   Ready    control-plane,master,worker   7h53m   v1.35.6   198.51.100.34   Red Hat Enterprise Linux CoreOS 9.8.20260915-0 (Plow)   5.14.0-687.48.1.el9_8.x86_64
ocp-node-2   Ready    control-plane,master,worker   8h      v1.35.6   198.51.100.35   Red Hat Enterprise Linux CoreOS 9.8.20260915-0 (Plow)   5.14.0-687.48.1.el9_8.x86_64
ocp-node-3   Ready    control-plane,master,worker   8h      v1.35.6   198.51.100.36   Red Hat Enterprise Linux CoreOS 9.8.20260915-0 (Plow)   5.14.0-687.48.1.el9_8.x86_64
```

**How to read it:** all nodes `Ready`. ROLES shows each node is control plane **and** worker (a compact 3-node cluster), so VMs run on all three. In production you usually have dedicated workers and the control-plane nodes show only `control-plane,master`.

```text
$ oc get co
NAME                                       VERSION   AVAILABLE   PROGRESSING   DEGRADED   SINCE
authentication                             4.22.15   True        False         False      152m
baremetal                                  4.22.15   True        False         False      8h
...
network                                    4.22.15   True        False         False      8h
storage                                    4.22.15   True        False         False      8h
```

(34 cluster operators, all `True False False`, trimmed here.)

**How to read it:** `co` = ClusterOperator, one per built-in component. Healthy is `AVAILABLE True, PROGRESSING False, DEGRADED False`. A quick filter that prints only unhealthy operators:

```text
$ oc get co --no-headers | awk '!($3=="True" && $4=="False" && $5=="False")'
$                       # no output = all healthy
```

### Check yourself

**Q. `oc get co` shows `machine-config` DEGRADED True. Should you start the wave?**
Answer: no. Machine Config applies OS changes to nodes and reboots them one at a time; a degraded MCO usually means a node is stuck mid-update. VMs on that node may be live-migrated or evicted unexpectedly. Fix it first.

## 8. Storage

### 8.1 Concept, from zero

| Object | Meaning | VMware analogy |
|---|---|---|
| CSI driver | A plugin that lets OpenShift create and attach disks on a storage system | The VASA provider / storage plugin |
| StorageClass (SC) | A named "type" of storage with parameters | A datastore or storage policy |
| PersistentVolumeClaim (PVC) | A request for a disk of a given size and access mode | Adding a hard disk to a VM |
| PersistentVolume (PV) | The actual disk the CSI driver created to satisfy the PVC | The VMDK file |
| DataVolume (DV) | OpenShift Virtualization helper that creates a PVC **and fills it** (import, clone, upload) | Deploy from template |
| StorageProfile | CDI's per-StorageClass hint of which access mode and volume mode to use for VM disks | |

**Access modes, the most important storage concept for VMs:**

| Mode | Short | Meaning | Live migration? |
|---|---|---|---|
| ReadWriteOnce | RWO | One node can mount it at a time | **No.** The VM is pinned to its node |
| ReadWriteMany | RWX | Many nodes can mount it at once | **Yes** |

**Why RWX matters:** live migration (the vMotion equivalent) starts the VM on the target node while it still runs on the source node. For a moment both nodes need the disk. If the disk is RWO, OpenShift cannot live-migrate the VM, and node maintenance (upgrades!) will shut the VM down instead of moving it.

**Volume modes:**

| Mode | Meaning | Typical for |
|---|---|---|
| Filesystem | The PV is a filesystem; the VM disk is a file `disk.img` on it | NFS |
| Block | The PV is a raw block device handed straight to the VM | FC and iSCSI LUNs; best performance |

### 8.2 Lab storage: NFS

The lab uses an NFS export on the bastion and the community `csi-driver-nfs` driver.

**Server side (bastion):**

```text
$ showmount -e localhost
Export list for localhost:
/exports/ocp 198.51.100.0/24

$ df -h /exports/ocp
Filesystem      Size  Used Avail Use% Mounted on
/dev/sdb        500G   20G  480G   4% /exports/ocp
```

`/etc/exports` contains `/exports/ocp 198.51.100.0/24(rw,sync,no_root_squash)`. `no_root_squash` is required because the CSI driver creates directories as root.

**Cluster side, GUI:** **Storage > StorageClasses**. `nfs-csi` is marked **Default**.

> **SCREENSHOT S-05:** Storage > StorageClasses list showing `nfs-csi` with the Default label.

**Cluster side, CLI:**

```text
$ oc get csidriver
NAME             ATTACHREQUIRED   PODINFOONMOUNT   STORAGECAPACITY   TOKENREQUESTS   REQUIRESREPUBLISH   MODES        AGE
nfs.csi.k8s.io   false            false            false             <unset>         false               Persistent   7h12m

$ oc get pods -n kube-system | grep csi-nfs
csi-nfs-controller-849b678558-dj4jp   5/5     Running   1 (154m ago)   158m
csi-nfs-node-jhcnx                    3/3     Running   0              7h13m
csi-nfs-node-smmxz                    3/3     Running   0              7h13m
csi-nfs-node-z8tfd                    3/3     Running   0              7h13m

$ oc get sc
NAME                PROVISIONER      RECLAIMPOLICY   VOLUMEBINDINGMODE   ALLOWVOLUMEEXPANSION   AGE
nfs-csi (default)   nfs.csi.k8s.io   Delete          Immediate           true                   7h12m
```

**How to read it:** one controller pod (creates and deletes volumes) and one node pod per node (mounts volumes on that node). The StorageClass uses that driver, deletes the data when the PVC is deleted (`Delete`), creates the volume as soon as the PVC appears (`Immediate`) and allows disks to grow.

The StorageClass definition:

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: nfs-csi
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: nfs.csi.k8s.io
parameters:
  server: 198.51.100.24
  share: /exports/ocp
  subDir: ${pvc.metadata.namespace}-${pvc.metadata.name}-${pv.metadata.name}
mountOptions:
- nfsvers=4.1
reclaimPolicy: Delete
volumeBindingMode: Immediate
allowVolumeExpansion: true
```

`subDir` makes each PVC its own folder named after project and PVC, so you can find a VM's disk on the NFS server by name.

**StorageProfile.** OpenShift Virtualization does not know what a community driver supports, so the lab tells it explicitly that `nfs-csi` volumes are RWX Filesystem:

```text
$ oc patch storageprofile nfs-csi --type merge \
  -p '{"spec":{"claimPropertySets":[{"accessModes":["ReadWriteMany"],"volumeMode":"Filesystem"}],"cloneStrategy":"copy"}}'

$ oc get storageprofile nfs-csi -o yaml | sed -n '/^status:/,$p'
status:
  claimPropertySets:
  - accessModes:
    - ReadWriteMany
    volumeMode: Filesystem
  cloneStrategy: copy
  conditions:
  - message: Provisioner is recognized
    reason: RecognizedProvisioner
    status: "True"
    type: Recognized
  provisioner: nfs.csi.k8s.io
  storageClass: nfs-csi
```

**Proof it works:** OpenShift Virtualization already imported its boot images onto this class, and the migrated test VM's disk is here too, all RWX:

```text
$ oc get pvc -A
NAMESPACE                            NAME                           STATUS   CAPACITY      ACCESS MODES   STORAGECLASS
mtv-test                             test-ubuntu-vm-2275-dm9s9      Bound    11381663335   RWX            nfs-csi
openshift-virtualization-os-images   centos-stream10-041e2e2ceec9   Bound    34144990004   RWX            nfs-csi
openshift-virtualization-os-images   rhel10-c5b97492a6e3            Bound    34144990004   RWX            nfs-csi
openshift-virtualization-os-images   rhel9-7005186c23b8             Bound    34144990004   RWX            nfs-csi
...
```

Note the migrated disk: the source VMDK is 10 GiB but the PVC is about 11.4 GB. CDI adds filesystem overhead (default 5.5%) so a 10 GiB disk image fits on a Filesystem-mode volume.

> **PRODUCTION:** the community NFS CSI driver is **not supported by Red Hat**. In production use your array vendor's certified CSI driver (NetApp Trident, Dell CSI PowerStore/PowerMax/PowerFlex, Pure Portworx/Pure CSI, HPE CSI, IBM Block CSI, Hitachi HSPC, etc.) for FC, iSCSI and NFS. Section 17 covers each.

### Check yourself

**Q1. A migrated VM's PVC is `Pending`. Where do you look?**
Answer: `oc describe pvc <name> -n <ns>` Events. Typical causes: no default StorageClass and none mapped, the CSI controller pod is down, the array is out of space, or `WaitForFirstConsumer` binding (normal: it binds when the importer pod is scheduled).

**Q2. Why does the lab set the StorageProfile by hand?**
Answer: CDI has a built-in list of known provisioners and their best access and volume modes. The community NFS driver is not on it, so without the profile CDI would not know to request RWX Filesystem, and VM disks could come out RWO (not live-migratable). Certified vendor drivers are usually recognised automatically.

**Q3. Is `reclaimPolicy: Delete` safe for VM disks?**
Answer: it means deleting the PVC deletes the data. That is normal and keeps storage clean, but a mistaken `oc delete pvc` is unrecoverable. For production, protect with backups (OADP), RBAC, or a `Retain` class for critical VMs.

## 9. VM networking

### 9.1 Concept

By default every pod and VM in OpenShift gets an address on the **pod network**, a private overlay (OVN-Kubernetes) that is NAT'd to the outside. That is fine for containers, but a migrated server VM usually needs to keep its **existing IP on its existing VLAN**. For that you give the VM a **secondary network** that is bridged straight onto a VLAN, just like a vSphere port group.

Three pieces, bottom to top:

| Layer | Object | Lab value | VMware analogy |
|---|---|---|---|
| Physical | Node NIC connected to a switch port (or vSphere port group) that carries the VLANs as a trunk | `ens34` on port group `PG-VM-TRUNK` (VLAN 4095) | Host uplink (vmnic) |
| Host switch | `NodeNetworkConfigurationPolicy` (NNCP) creating a Linux bridge on that NIC on every node | `br-vm-ens34` creates `br-vm`, trunk VLANs 2 to 4094 | vSwitch / VDS |
| VM network | `NetworkAttachmentDefinition` (NAD) that says "bridge br-vm, VLAN X" | `vlan-110`, `vlan-120`, `vlan-130` | Port group |

The NMState operator applies NNCPs to nodes and reports the result per node as `NodeNetworkConfigurationEnactment` (NNCE). It also publishes each node's current network state as `NodeNetworkState` (NNS).

**Lab-only note (nested):** because the OpenShift nodes are themselves vSphere VMs, the vSphere port group behind `ens34` must have **Promiscuous mode, MAC address changes and Forged transmits = Accept**, and must be VLAN 4095 (trunk). Otherwise vSphere drops frames from VM MAC addresses it does not know. On bare metal this does not apply; the physical switch port is simply configured as a trunk.

### 9.2 GUI

1. **Networking > NodeNetworkConfigurationPolicy > Create**. Policy name `br-vm-ens34`, node selector `node-role.kubernetes.io/worker`, interface type Linux bridge, name `br-vm`, port `ens34`, IPv4 and IPv6 off, STP off. (The form's VLAN trunk options are limited; for trunks, switch to **YAML view** and paste the YAML in 9.3.)
2. Wait until **Status** shows **Available** for every node.
3. **Networking > NetworkAttachmentDefinitions > Create**, project `default`. Name `vlan-110`, Network Type **Linux bridge**, Bridge name `br-vm`, VLAN tag `110`, MAC spoof check on.

> **SCREENSHOT S-06:** Networking > NodeNetworkConfigurationPolicy showing `br-vm-ens34` Available, with the per-node enactments expanded.
>
> **SCREENSHOT S-07:** Networking > NodeNetworkState > `ocp-node-1` showing `br-vm` (linux-bridge) and `ens34`.
>
> **SCREENSHOT S-08:** The NetworkAttachmentDefinition create form filled in for `vlan-110`.

### 9.3 CLI

The policy (from `install/vm-networks.sh`):

```yaml
apiVersion: nmstate.io/v1
kind: NodeNetworkConfigurationPolicy
metadata:
  name: br-vm-ens34
spec:
  nodeSelector:
    node-role.kubernetes.io/worker: ""
  desiredState:
    interfaces:
    - name: br-vm
      type: linux-bridge
      state: up
      ipv4: {enabled: false}        # the bridge carries VM traffic only; no node IP on it
      ipv6: {enabled: false}
      bridge:
        options: {stp: {enabled: false}}
        port:
        - name: ens34
          vlan:
            mode: trunk               # pass tagged frames for many VLANs
            trunk-tags:
            - id-range: {min: 2, max: 4094}
```

```text
$ oc apply -f br-vm-ens34.yaml
$ oc get nncp
NAME          STATUS      REASON
br-vm-ens34   Available   SuccessfullyConfigured

$ oc get nnce
NAME                    STATUS      STATUS AGE   REASON
ocp-node-1.br-vm-ens34   Available   148m         SuccessfullyConfigured
ocp-node-2.br-vm-ens34   Available   175m         SuccessfullyConfigured
ocp-node-3.br-vm-ens34   Available   175m         SuccessfullyConfigured
```

**How to read it:** the policy is Available and each node has an enactment that succeeded. If one node fails, NMState rolls that node back automatically (so a bad policy cannot cut a node off), and the NNCE for that node shows `Failing` with the reason.

What a node now looks like:

```text
$ oc get nns ocp-node-1 -o jsonpath='{range .status.currentState.interfaces[*]}{.name}{"\t"}{.type}{"\t"}{.state}{"\n"}{end}' | grep -v -E 'veth|genev|ovn|ovs-sys'
br-ex   ovs-bridge      up
br-ex   ovs-interface   up
br-int  ovs-interface   ignore
br-vm   linux-bridge    up
ens33   ethernet        up
ens34   ethernet        up
lo      loopback        up
```

`ens33` is enslaved to `br-ex` (the OVS bridge that carries the node IP and pod network), and `ens34` is enslaved to the new `br-vm`.

The NADs:

```yaml
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata:
  name: vlan-110
  namespace: default
  annotations:
    k8s.v1.cni.cncf.io/resourceName: bridge.network.kubevirt.io/br-vm
spec:
  config: '{"cniVersion":"0.3.1","name":"vlan-110","type":"bridge","bridge":"br-vm","vlan":110,"macspoofchk":true,"ipam":{}}'
```

```text
$ oc get net-attach-def -A
NAMESPACE                  NAME         AGE
default                    vlan-120   175m
default                    vlan-130   175m
default                    vlan-110    175m
openshift-ovn-kubernetes   default      8h
```

**Why `namespace: default`:** a NAD in the `default` project can be used by VMs in any project. Put a NAD in a specific project if only that project should use the VLAN.

**Why `"ipam":{}`:** OpenShift does not hand out IPs on this network. The guest keeps its static IP or gets one from the VLAN's existing DHCP server, exactly as on VMware.

**The `resourceName` annotation** tells the scheduler to place VMs only on nodes that actually have `br-vm`, so a VM never lands on a node without the bridge.

> **PRODUCTION:** use a bond (`type: bond`, mode `802.3ad` with LACP agreed with the network team, or `active-backup`) of two NICs as the bridge port instead of a single NIC. Alternatively use OVN-Kubernetes **localnet** secondary networks (`type: ovn-k8s-cni-overlay`, `topology: localnet`) with an OVS bridge mapping, which supports network policies on VM traffic. Agree the VLAN list with the network team and configure the switch ports as trunks with exactly those VLANs allowed.

### Check yourself

**Q1. You map port group VM-Network-110 to the Pod network instead of `vlan-110`. What happens to the VM?**
Answer: it boots with a NIC on the pod network, gets a 10.128.x.x/10.13x.x.x address via DHCP from OVN, and is reachable only through OpenShift Services and Routes. Its old 198.51.100.x address is gone. Map to the bridge NAD to keep layer-2 adjacency and the IP.

**Q2. The NNCP is Available on two nodes and Failing on one. What did NMState do on the failing node?**
Answer: it rolled that node back to its previous network configuration after the probe (connectivity to the API and default gateway) failed. Read the NNCE message, fix the cause (wrong NIC name on that node, NIC down), and the policy re-applies.

**Q3. Why disable IPv4 on `br-vm`?**
Answer: the bridge only switches VM frames. Giving it an IP would create a second path for node traffic, can break routing, and on the lab VLANs would need an address you do not have.

## 10. Install OpenShift Virtualization, NMState and MTV

### 10.1 Concept

All three are **operators**: software that installs and then keeps managing a product. Installing is always two steps:

1. Install the operator (a **Subscription** in a namespace with an **OperatorGroup**). OLM installs it and it shows as a **ClusterServiceVersion (CSV)** in phase `Succeeded`.
2. Create the operator's main custom resource, which tells it to deploy the product: `HyperConverged` (OpenShift Virtualization), `NMState`, `ForkliftController` (MTV).

| Product | Namespace | Package | Channel used | Main CR |
|---|---|---|---|---|
| OpenShift Virtualization | `openshift-cnv` | `kubevirt-hyperconverged` | `stable` | `HyperConverged` `kubevirt-hyperconverged` |
| Kubernetes NMState | `openshift-nmstate` | `kubernetes-nmstate-operator` | `stable` | `NMState` `nmstate` |
| Migration Toolkit for Virtualization | `openshift-mtv` | `mtv-operator` | `release-v2.12` | `ForkliftController` `forklift-controller` |

### 10.2 GUI

1. **Ecosystem > Software Catalog** (called **Operators > OperatorHub** in older consoles). Search **OpenShift Virtualization** > **Install**. Keep the defaults (namespace `openshift-cnv`, channel `stable`, approval Automatic) > **Install**.
2. When it shows **Ready for use**, click **Create HyperConverged** > keep defaults > **Create**. Wait until the status is **Available** (10 to 15 minutes).
3. Repeat for **Kubernetes NMState Operator** (create the `NMState` instance) and **Migration Toolkit for Virtualization Operator** (namespace `openshift-mtv`, create the `ForkliftController`).
4. Refresh the browser. New menus appear: **Virtualization** and **Migration for Virtualization**.

> **SCREENSHOT S-09:** Software Catalog search result for OpenShift Virtualization with the Install button.
>
> **SCREENSHOT S-10:** Installed Operators in all namespaces showing OpenShift Virtualization 4.22.9, Kubernetes NMState and MTV 2.12.9 all Succeeded.
>
> **SCREENSHOT S-11:** Left navigation showing the Virtualization and Migration for Virtualization menus.

**PRODUCTION:** set **Update approval** to **Manual** for OpenShift Virtualization and MTV, so operator upgrades happen in a change window and never during a migration wave.

### 10.3 CLI

One function installs any operator (from `install/install-cnv-nmstate-mtv.sh`):

```bash
sub() { # namespace package channel
cat <<Y | oc apply -f -
apiVersion: v1
kind: Namespace
metadata: {name: $1}
---
apiVersion: operators.coreos.com/v1
kind: OperatorGroup
metadata: {name: $1-og, namespace: $1}
spec: {targetNamespaces: [$1]}
---
apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata: {name: $2, namespace: $1}
spec: {source: redhat-operators, sourceNamespace: openshift-marketplace, name: $2, channel: $3, installPlanApproval: Automatic}
Y
}
sub openshift-cnv     kubevirt-hyperconverged     stable
sub openshift-nmstate kubernetes-nmstate-operator stable
sub openshift-mtv     mtv-operator                release-v2.12
```

Then the three main CRs:

```yaml
apiVersion: hco.kubevirt.io/v1beta1
kind: HyperConverged
metadata: {name: kubevirt-hyperconverged, namespace: openshift-cnv}
spec: {}
---
apiVersion: nmstate.io/v1
kind: NMState
metadata: {name: nmstate}
---
apiVersion: forklift.konveyor.io/v1beta1
kind: ForkliftController
metadata: {name: forklift-controller, namespace: openshift-mtv}
spec: {olm_managed: true}
```

```text
$ oc wait hyperconverged/kubevirt-hyperconverged -n openshift-cnv --for=condition=Available --timeout=900s
```

### 10.4 Verify (real lab output)

```text
$ oc get csv -n openshift-cnv
NAME                                       DISPLAY                    VERSION   REPLACES                                   PHASE
kubevirt-hyperconverged-operator.v4.22.9   OpenShift Virtualization   4.22.9    kubevirt-hyperconverged-operator.v4.22.6   Succeeded

$ oc get csv -n openshift-mtv
NAME                   DISPLAY                                         VERSION   REPLACES               PHASE
mtv-operator.v2.12.9   Migration Toolkit for Virtualization Operator   2.12.9    mtv-operator.v2.12.8   Succeeded

$ oc get csv -n openshift-nmstate
NAME                                              DISPLAY                       VERSION               PHASE
kubernetes-nmstate-operator.4.22.0-202609230131   Kubernetes NMState Operator   4.22.0-202609230131   Succeeded

$ oc get hco kubevirt-hyperconverged -n openshift-cnv -o jsonpath='{range .status.conditions[*]}{.type}={.status}{"\n"}{end}'
ReconcileComplete=True
Available=True
Progressing=False
Degraded=False
Upgradeable=True

$ oc get nmstate
NAME      STATUS      REASON
nmstate   Available   SuccessfullyDeployed

$ oc get pods -n openshift-mtv
NAME                                                    READY   STATUS    RESTARTS   AGE
forklift-api-97fbd7cf-bzwh2                             1/1     Running   0          7h7m
forklift-cli-download-77d8546b5f-d9sxh                  1/1     Running   0          7h8m
forklift-controller-5f6b76dd7c-d9l8z                    2/2     Running   0          157m
forklift-operator-845fbf655b-47547                      1/1     Running   0          157m
forklift-ova-proxy-7f6dd84f44-pdtln                     1/1     Running   0          157m
forklift-ui-plugin-7d8fb5df84-kt6xj                     1/1     Running   0          157m
forklift-validation-7bf5749c7b-lng6x                    1/1     Running   0          7h8m
forklift-volume-populator-controller-685dc5768f-2mz58   1/1     Running   0          157m
```

**How to read the MTV pods:**

| Pod | Job |
|---|---|
| `forklift-controller` | The brain: runs plans and migrations, and the inventory that reads vCenter |
| `forklift-api` | Admission webhooks that validate your objects |
| `forklift-validation` | Policy checks on source VMs (warnings such as "shared disk", "RDM", "CBT disabled") |
| `forklift-ui-plugin` | The Migration for Virtualization pages in the console |
| `forklift-volume-populator-controller` | Fills PVCs from the source (used by storage offload and other populators) |
| `forklift-cli-download` | Serves the `kubectl-mtv` CLI plugin for download |
| `forklift-ova-proxy` | Used for OVA imports |

OpenShift Virtualization also imported its golden images, which proves storage works end to end:

```text
$ oc get datasource -n openshift-virtualization-os-images
NAME              AGE
centos-stream10   7h8m
centos-stream9    7h8m
fedora            7h8m
rhel10            7h8m
rhel7             7h8m
rhel8             7h8m
rhel9             7h8m
win10             7h8m
...
win2k25           7h8m
```

### Check yourself

**Q1. The OpenShift Virtualization CSV is Succeeded but there is no Virtualization menu. What is missing?**
Answer: the `HyperConverged` CR. The CSV is only the operator; it deploys nothing until you create its CR. Then refresh the console so the plugin loads.

**Q2. Why pin MTV to a `release-v2.x` channel and Manual approval in production?**
Answer: MTV updates can change plan behaviour and default settings. You want the same MTV version for a whole wave, upgraded deliberately after reading the release notes, not mid-migration.

# Part C: Migrate virtual machines (GUI and CLI side by side)

## 11. vCenter service account for MTV

### 11.1 Concept

MTV logs in to vCenter with an account, reads the inventory, takes snapshots (warm migration), reads disks, and powers the source VM off at cutover. Give it a dedicated service account with exactly the privileges it needs, not `administrator@vsphere.local`.

**Why:** least privilege limits the damage if the credential leaks, and a named account makes MTV's actions easy to find in vCenter's task and event log.

### 11.2 Privileges

The lab role `MTV-Migration` has these 20 privileges (created by `migration/scripts/New-MtvVcenterAccount.ps1`):

| Group | Privileges | Why MTV needs it |
|---|---|---|
| Sessions | `Sessions.ValidateSession` | Keep its API session alive |
| Datastore | `Datastore.Browse`, `Datastore.FileManagement` | Find and read VMDK files |
| Virtual machine > Change configuration | `VirtualMachine.Config.ChangeTracking` | Turn on CBT for warm migration |
| Virtual machine > Interaction | `VirtualMachine.Interact.PowerOff`, `VirtualMachine.Interact.PowerOn` | Shut down the source at cutover; power it back on for rollback |
| Virtual machine > Guest operations | `VirtualMachine.GuestOperations.Query`, `.Execute`, `.Modify` | Read guest IP configuration to preserve static IPs |
| Virtual machine > Provisioning | `VirtualMachine.Provisioning.DiskRandomAccess`, `.DiskRandomRead`, `.FileRandomAccess`, `.GetVmFiles`, `.PutVmFiles`, `.Clone`, `.MarkAsTemplate` | Read disks through VDDK / NFC |
| Virtual machine > Snapshot management | `VirtualMachine.State.CreateSnapshot`, `.RemoveSnapshot` | Consistent reads and CBT deltas |
| Cryptographic operations | `Cryptographer.Access`, `Cryptographer.Decrypt` | Only if you migrate encrypted VMs |

### 11.3 GUI (vSphere Client)

1. **Administration > Single Sign On > Users and Groups > vsphere.local > Add**: user `mtv`, a strong password.
2. **Administration > Access Control > Roles > New**: name `MTV-Migration`, tick the privileges above.
3. **Hosts and Clusters**, select the vCenter object (or the datacenter / folder that holds the VMs to migrate) > **Permissions > Add**: user `mtv@vsphere.local`, role `MTV-Migration`, **Propagate to children** ticked.
4. Log in to the vSphere Client as `mtv@vsphere.local` and confirm you can see the VMs.

> **SCREENSHOT S-12:** vSphere Client role editor showing role MTV-Migration with its privileges ticked.
>
> **SCREENSHOT S-13:** vCenter Permissions tab showing mtv@vsphere.local with role MTV-Migration, propagated.

### 11.4 CLI (PowerCLI on the Windows jump host)

```powershell
# Run from PowerShell with VMware PowerCLI; prompts for credentials, writes nothing to disk
.\New-MtvVcenterAccount.ps1 -VCenter vcenter.example.com -SkipCertificateCheck
```

The script creates the role from the privilege list, creates the SSO user and adds the propagated permission at the vCenter root. It is idempotent: running it again updates the role.

**PRODUCTION:** scope the permission to the folders or clusters being migrated rather than the vCenter root, store the password in your vault, and rotate it after the project.

## 12. VDDK image (fast disk reads)

### 12.1 Concept

**VDDK** (Virtual Disk Development Kit) is VMware's library for reading VM disks straight from the ESXi host, the same one backup products use. Red Hat cannot ship it, so you download it from Broadcom and wrap it in a small container image. MTV runs that image as an init container in every disk-transfer pod.

**Why it matters, as seen in the lab:** the lab provider has **no** VDDK image, so MTV fell back to reading the disk over HTTPS through vCenter (`nbdkit curl` in the log). The 10 GiB test disk spent 23 minutes in image conversion and then wrote at roughly 2 MB/s (512 MB in about 4 minutes):

```text
ImageConversion   Completed   0/1           2026-10-05T15:08:21Z   2026-10-05T15:31:47Z
DiskTransferV2v   Running     1740/10240    2026-10-05T15:31:47Z

$ oc logs <migration pod> -n mtv-test --tail=5
nbdkit: curl[4]: debug: curl: running cookie-script
nbdkit: curl[4]: debug: cookie-script returned cookies
▀  17% [*******---------------------------------]
virt-v2v monitoring: Progress update, completed 17 %
```

With VDDK the same copy runs over NBD/NFC from the ESXi host at near line rate, and **warm migration requires it** in practice. Use VDDK for every production migration.

### 12.2 Build and push the image (bastion)

1. Download the Linux VDDK tarball for your vSphere major version (for example `VMware-vix-disklib-8.0.x-NNNNNNNN.x86_64.tar.gz`) from the Broadcom developer portal and copy it to the bastion.
2. If the **Create provider** form in your MTV version offers **Upload VDDK tarball**, upload it there and skip to 12.3. Otherwise:

```bash
# Expose the internal image registry once (it needs storage on a bare-metal style install)
oc patch configs.imageregistry.operator.openshift.io cluster --type merge \
  -p '{"spec":{"managementState":"Managed","storage":{"pvc":{"claim":""}},"defaultRoute":true}}'
REG=$(oc get route default-route -n openshift-image-registry -o jsonpath='{.spec.host}')

mkdir ~/vddk && cd ~/vddk
tar -xzf ~/VMware-vix-disklib-*.x86_64.tar.gz          # creates vmware-vix-disklib-distrib/
cat > Containerfile <<'EOF'
FROM registry.access.redhat.com/ubi9/ubi-minimal
USER 1001
COPY vmware-vix-disklib-distrib /vmware-vix-disklib-distrib
RUN mkdir -p /opt
ENTRYPOINT ["cp", "-r", "/vmware-vix-disklib-distrib", "/opt"]
EOF
podman build -t $REG/openshift-mtv/vddk:8.0 .
podman login -u $(oc whoami) -p $(oc whoami -t) --tls-verify=false $REG
podman push --tls-verify=false $REG/openshift-mtv/vddk:8.0
oc get imagestream vddk -n openshift-mtv
```

**Why the entrypoint is just `cp`:** the image's only job is to drop the VDDK library into a shared folder so the disk-copy container can load it.

### 12.3 Use it in the provider

Inside the cluster the image is addressed by the internal service name:

```text
image-registry.openshift-image-registry.svc:5000/openshift-mtv/vddk:8.0
```

**GUI:** Providers > `vcenter-lab` > **Edit** > **VDDK init image** field. **CLI:**

```bash
oc patch provider vcenter-lab -n openshift-mtv --type merge \
  -p '{"spec":{"settings":{"vddkInitImage":"image-registry.openshift-image-registry.svc:5000/openshift-mtv/vddk:8.0"}}}'
```

**PRODUCTION:** push the VDDK image to your corporate registry (Quay, Artifactory, Harbor) instead of the internal registry, and keep the image version in the change record. Keep `--tls-verify=false` out of production; trust the registry CA on the bastion instead.

## 13. Prepare the source VMs

Run this for every VM in a wave. A VM that fails a check is moved to a later wave, not forced.

### 13.1 Checks in the vSphere Client

| # | Check | Why | Fix |
|---|---|---|---|
| V1 | No existing snapshots | MTV takes its own; long chains slow reads and can break CBT | Snapshots > Delete All, then Consolidate if prompted |
| V2 | VMware Tools running | Clean shutdown at cutover, guest IP information | Install/upgrade Tools |
| V3 | Guest OS supported by virt-v2v | Conversion must recognise the OS | Check the MTV supported guest list (RHEL 7 to 10, Windows Server 2012 R2 to 2025, Windows 10/11, Ubuntu, SLES, etc.) |
| V4 | RDM or independent disks | RDM data is not copied; independent disks are skipped by snapshots (no warm) | Convert RDMs to virtual disks with Storage vMotion, or present the same LUN to OpenShift and use the plan option `rdmAsLun` (section 17.6) |
| V5 | Shared disks (multi-writer, clusters) | Need a separate plan for the shared disk | Plan with `migrateSharedDisks`, migrate cluster members together |
| V6 | CBT enabled (warm only) | Delta copies need it | `ctkEnabled = TRUE` and `scsiX:Y.ctkEnabled = TRUE`, then a stun cycle |
| V7 | Disk count and size recorded | Capacity planning on the target StorageClass | |
| V8 | Encryption (vTPM, VM encryption, BitLocker) | Needs key handling | Suspend BitLocker; plan for encrypted VMs |
| V9 | Network baseline recorded | Proof after cutover | Table below |

> **SCREENSHOT S-14:** Source VM Summary tab showing VMware Tools running, no snapshots, and the network adapter on port group VM-Network-110.
>
> **SCREENSHOT S-15:** Edit Settings > Advanced Parameters with `ctkEnabled = TRUE` and `scsi0:0.ctkEnabled = TRUE`.

### 13.2 Turn on CBT with PowerCLI (no power-off)

```powershell
$vm = Get-VM '<VM_NAME>'
$spec = New-Object VMware.Vim.VirtualMachineConfigSpec
$spec.ChangeTrackingEnabled = $true
$vm.ExtensionData.ReconfigVM($spec)
# CBT becomes active after a stun cycle: create and delete a snapshot
New-Snapshot -VM $vm -Name cbt-activate | Remove-Snapshot -Confirm:$false
# verify: each disk now has a -ctk.vmdk file in the datastore folder
```

### 13.3 Guest preparation

**Linux:** record `ip -4 addr; ip route; cat /etc/resolv.conf`. Check the VirtIO drivers are available (`lsinitrd | grep virtio` on RHEL; standard on RHEL 8 and later and on current Ubuntu). Prefer NetworkManager profiles bound to the MAC address (MTV keeps the MAC).

**Windows:** disable Fast Startup and hibernation (`powercfg /h off`), suspend BitLocker, record static IP settings (`ipconfig /all`). virt-v2v injects the VirtIO drivers during conversion; you do not install them beforehand.

### 13.4 Baseline record (fill in per VM)

| Field | Before (VMware) | After (OpenShift) |
|---|---|---|
| VM name | | |
| vCPU / RAM | | |
| Disks (count, sizes) | | |
| IP / mask / gateway / DNS | | |
| MAC address | | |
| Services that must be running | | |
| Application smoke test | | |

## 14. Providers and maps

### 14.1 Concept

| MTV object | Meaning | Lab name |
|---|---|---|
| **Provider** (source) | Connection to vCenter (URL, credentials secret, VDDK image) | `vcenter-lab` |
| **Provider** (destination) | This OpenShift cluster. Created automatically | `host` |
| **NetworkMap** | Each source port group mapped to a target network (Pod network or a NAD) | `vcenter-lab-net` |
| **StorageMap** | Each source datastore mapped to a StorageClass, with access mode and volume mode | `vcenter-lab-storage` |
| **Plan** | Which VMs, which maps, which target project, cold or warm | `test-ubuntu` |
| **Migration** | One run of a plan (the Start button) | `test-ubuntu-1` |

Maps are reusable: one NetworkMap and one StorageMap per source vCenter usually serve every plan in a wave.

### 14.2 Create the vSphere provider

**GUI:** **Migration for Virtualization > Providers > Create provider > VMware**.

| Field | Lab value |
|---|---|
| Name | `vcenter-lab` |
| Project | `openshift-mtv` |
| Endpoint type | vCenter |
| URL | `https://vcenter.example.com/sdk` |
| VDDK init image | empty in the lab (see section 12); set it in production |
| Username / password | `mtv@vsphere.local` / its password |
| Certificate | Skip certificate validation (lab only); in production paste the vCenter CA or accept the shown fingerprint |

Click **Create provider** and wait for **Status Ready**. The provider page then shows inventory counts (VMs, hosts, networks, datastores).

> **SCREENSHOT S-16:** Create provider form (VMware) filled in, password field blurred.
>
> **SCREENSHOT S-17:** Providers list showing `host` (OpenShift) and `vcenter-lab` (VMware) both Ready, with the vcenter-lab inventory counts.

**CLI** (`install/add-mtv-vsphere-provider.sh`; prompts for the password and writes nothing to disk):

```bash
read -rsp "Password for mtv@vsphere.local: " PW; echo
oc create secret generic vcenter-lab -n openshift-mtv \
  --from-literal=user=mtv@vsphere.local --from-literal=password="$PW" \
  --from-literal=url=https://vcenter.example.com/sdk --from-literal=insecureSkipVerify=true \
  --dry-run=client -o yaml \
  | oc label -f - --local -o yaml createdForResourceType=providers createdForProviderType=vsphere \
  | oc apply -f -
unset PW
cat <<'Y' | oc apply -f -
apiVersion: forklift.konveyor.io/v1beta1
kind: Provider
metadata: {name: vcenter-lab, namespace: openshift-mtv}
spec:
  type: vsphere
  url: https://vcenter.example.com/sdk
  secret: {name: vcenter-lab, namespace: openshift-mtv}
  settings:
    sdkEndpoint: vcenter
    # vddkInitImage: <registry>/openshift-mtv/vddk:8.0     # production
Y
oc wait provider/vcenter-lab -n openshift-mtv --for=condition=Ready --timeout=300s
```

**Why the two labels:** MTV's admission webhook only accepts a provider secret labelled with both `createdForResourceType` and `createdForProviderType`.

**Output (lab):**

```text
$ oc get providers -n openshift-mtv
NAME          TYPE        STATUS   READY   CONNECTED   INVENTORY   URL                                        AGE
host          openshift   Ready    True    True        True                                                   7h8m
vcenter-lab   vsphere     Ready    True    True        True        https://vcenter.example.com/sdk   57m

$ oc get provider vcenter-lab -n openshift-mtv -o yaml | sed -n '/^status:/,$p'
status:
  conditions:
  - category: Warn
    message: TLS is susceptible to machine-in-the-middle attacks when certificate
      verification is skipped.
    reason: SkipTLSVerification
    type: ConnectionInsecure
  - category: Required
    message: Connection test, succeeded.
    type: ConnectionTestSucceeded
  - category: Advisory
    message: Validation has been completed.
    type: Validated
  - category: Required
    message: The inventory has been loaded.
    type: InventoryCreated
  - category: Required
    message: The provider is ready.
    type: Ready
  fingerprint: AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD
  phase: Ready
```

**How to read it:** `CONNECTED True` = login works; `INVENTORY True` = MTV has read vCenter's VMs, networks and datastores. The `ConnectionInsecure` warning is expected in the lab because certificate checking is skipped. The fingerprint is the vCenter certificate's SHA-1; in production compare it with the one in the vSphere Client before trusting it.

**Secret hygiene:** the secret holds `user`, `password`, `url` and `insecureSkipVerify`. Never print it with `-o yaml` in a shared session; to check it exists, list only the keys:

```text
$ oc get secret vcenter-lab -n openshift-mtv -o jsonpath='{.data}' | python3 -c 'import json,sys; print(sorted(json.load(sys.stdin).keys()))'
['insecureSkipVerify', 'password', 'url', 'user']
```

### 14.3 NetworkMap

**GUI:** **Migration for Virtualization > Network maps > Create network map** (or let the Create plan wizard create one). Source provider `vcenter-lab`, target `host`. Map source network **VM-Network-110** to **default / vlan-110** (type Multus).

> **SCREENSHOT S-18:** Network map editor showing 110 mapped to default/vlan-110.

**CLI:**

```yaml
apiVersion: forklift.konveyor.io/v1beta1
kind: NetworkMap
metadata: {name: vcenter-lab-net, namespace: openshift-mtv}
spec:
  provider:
    source: {name: vcenter-lab, namespace: openshift-mtv}
    destination: {name: host, namespace: openshift-mtv}
  map:
  - source: {name: "VM-Network-110"}                                        # vSphere port group name
    destination: {type: multus, namespace: default, name: vlan-110}
  # more rows, one per port group used by the VMs in the plan:
  # - source: {name: "VM-Prod-120"}
  #   destination: {type: multus, namespace: default, name: vlan-120}
  # - source: {name: "Isolated"}
  #   destination: {type: pod}                                    # pod network
```

**Rules:** every port group used by any NIC of any VM in the plan must have a row, or the plan will not become Ready. Two NICs of one VM can map to the pod network only once (a VM can have only one pod-network NIC).

### 14.4 StorageMap

**GUI:** **Migration for Virtualization > Storage maps > Create storage map**. Map datastore **DATASTORE-01** to StorageClass **nfs-csi**.

> **SCREENSHOT S-19:** Storage map editor showing DATASTORE-01 mapped to nfs-csi.

**CLI:**

```yaml
apiVersion: forklift.konveyor.io/v1beta1
kind: StorageMap
metadata: {name: vcenter-lab-storage, namespace: openshift-mtv}
spec:
  provider:
    source: {name: vcenter-lab, namespace: openshift-mtv}
    destination: {name: host, namespace: openshift-mtv}
  map:
  - source: {name: DATASTORE-01}                # vSphere datastore name
    destination:
      storageClass: nfs-csi
      accessMode: ReadWriteMany                          # live-migratable
      volumeMode: Filesystem                             # NFS = Filesystem
```

`accessMode` and `volumeMode` are optional; if left out, MTV uses the StorageProfile defaults. Setting them makes the intent explicit, which matters most in production where FC and iSCSI classes should be `Block` (section 17).

```text
$ oc get networkmaps,storagemaps -n openshift-mtv
NAME                                              READY   AGE
networkmap.forklift.konveyor.io/vcenter-lab-net   True    22m

NAME                                                  READY   AGE
storagemap.forklift.konveyor.io/vcenter-lab-storage   True    22m
```

## 15. Plan, migrate, validate, roll back

### 15.1 Create the plan

**GUI:** **Migration for Virtualization > Migration plans > Create plan**.

1. **Source provider:** `vcenter-lab`. Search and tick the VMs.
2. **Plan name:** e.g. `wave01-app-web`. **Target provider:** `host`. **Target project:** the OpenShift project for these VMs (create it first: `oc new-project <name>`).
3. **Network map / Storage map:** pick the existing maps (or create new in the wizard).
4. **Migration type:** Cold or Warm.
5. **Other settings** (review them): preserve static IPs (on by default), target power state, delete VMs on failed migration, run pre-flight inspection, and transfer network.
6. **Create**. The plan validates; fix every **Critical** concern before you start.

> **SCREENSHOT S-20:** Create plan wizard, VM selection step with the test VM ticked.
>
> **SCREENSHOT S-21:** Plan details page showing Ready, the maps, and any validation concerns.

**CLI:**

```yaml
apiVersion: forklift.konveyor.io/v1beta1
kind: Plan
metadata: {name: test-ubuntu, namespace: openshift-mtv}
spec:
  warm: false                     # true for warm migration
  targetNamespace: mtv-test
  provider:
    source: {name: vcenter-lab, namespace: openshift-mtv}
    destination: {name: host, namespace: openshift-mtv}
  map:
    network: {name: vcenter-lab-net, namespace: openshift-mtv}
    storage: {name: vcenter-lab-storage, namespace: openshift-mtv}
  vms:
  - name: ubuntu-noble-24.04-cloudimg     # or  - id: vm-2275  (the vSphere MoRef)
```

After `oc apply`, MTV fills in defaults. The real plan in the lab:

```text
$ oc get plan test-ubuntu -n openshift-mtv -o yaml | sed -n '/^spec:/,/^status:/p'
spec:
  deleteVmOnFailMigration: true
  map:
    network: {name: vcenter-lab-net, namespace: openshift-mtv}
    storage: {name: vcenter-lab-storage, namespace: openshift-mtv}
  migrateSharedDisks: true
  preserveStaticIPs: true
  provider:
    destination: {name: host, namespace: openshift-mtv}
    source: {name: vcenter-lab, namespace: openshift-mtv}
  pvcNameTemplateUseGenerateName: true
  runPreflightInspection: true
  skipGuestConversion: false
  targetNamespace: mtv-test
  useCompatibilityMode: true
  vms:
  - name: ubuntu-noble-24.04-cloudimg
  warm: false
  xfsCompatibility: false
```

| Field | Meaning | Recommendation |
|---|---|---|
| `preserveStaticIPs` | Re-applies the guest's static IP after conversion (needs VMware Tools data) | Keep `true` |
| `deleteVmOnFailMigration` | Removes the half-created target VM if the run fails | `true` keeps retries clean |
| `runPreflightInspection` | Inspects the guest disk before conversion and fails fast on problems | Keep `true` |
| `migrateSharedDisks` | Copies shared disks with this plan | Set `false` on all but one plan when cluster members share disks |
| `useCompatibilityMode` | Only matters with `skipGuestConversion: true` (raw copy): `true` uses SATA disks and E1000E NICs so the VM boots without VirtIO drivers | Leave the default; normal conversion always uses VirtIO |
| `skipGuestConversion` | Copies disks without virt-v2v (raw copy) | Only for appliances you will fix yourself |
| `targetPowerState` | `on`, `off` or `auto` (default: match the source VM's power state) | `off` for production until the app team is ready |
| `type` | `cold`, `warm`, `live` or `conversion`; supersedes the older `warm: true/false` field | `cold` or `warm` for vSphere sources |
| `transferNetwork` | A NAD used for disk transfer traffic instead of the pod network | Use a dedicated migration VLAN in production (section 17.7) |
| `convertorNodeSelector` | Pins virt-v2v conversion pods to chosen nodes | Keep conversion load off busy nodes |
| `rdmAsLun`, `scsiReservation` | Pass RDMs as LUNs and allow SCSI reservations (Windows clusters) | Only with the storage team, section 17.6 |

### 15.2 Start a cold migration

**GUI:** on the plan click **Start** > confirm. **CLI:** create a Migration object:

```yaml
apiVersion: forklift.konveyor.io/v1beta1
kind: Migration
metadata: {name: test-ubuntu-1, namespace: openshift-mtv}
spec:
  plan: {name: test-ubuntu, namespace: openshift-mtv}
```

```text
$ oc apply -f test-ubuntu-migration.yaml
$ oc get plan,migration -n openshift-mtv
NAME                                    READY   EXECUTING   SUCCEEDED   FAILED   AGE
plan.forklift.konveyor.io/test-ubuntu   True    True                             29m

NAME                                           READY   RUNNING   SUCCEEDED   FAILED   AGE
migration.forklift.konveyor.io/test-ubuntu-1   True    True                           28m
```

To run the same plan again (after a failure), create a new Migration with a new name (`test-ubuntu-2`).

### 15.3 Watch progress

**GUI:** open the plan > **VMs** tab > expand the VM to see each pipeline step with a progress bar.

> **SCREENSHOT S-22:** Plan VMs tab with the pipeline expanded, showing Initialize and DiskAllocation completed and ImageConversion running.

**CLI:** one line per pipeline step:

```text
$ oc get plan test-ubuntu -n openshift-mtv -o jsonpath='{range .status.migration.vms[*].pipeline[*]}{.name}{"\t"}{.phase}{"\t"}{.progress.completed}/{.progress.total}{"\n"}{end}'
Initialize               Completed   0/1
DiskAllocation           Completed   10240/10240
ImageConversion          Completed   0/1
DiskTransferV2v          Running     1740/10240
VirtualMachineCreation   Pending     0/1
```

| Step | What happens | Where to look if stuck |
|---|---|---|
| Initialize | Validates, creates the target project objects | `oc describe migration`, forklift-controller log |
| DiskAllocation | Creates PVCs (DataVolumes) on the target StorageClass and copies the disk data | `oc get dv,pvc -n <target>`; importer pod logs |
| ImageConversion | Runs virt-v2v in a pod: inspects the guest, injects VirtIO, fixes boot | `oc logs <plan-vm-xxxx pod> -n <target>` |
| DiskTransferV2v | virt-v2v writes the converted disk | same pod |
| VirtualMachineCreation | Creates the `VirtualMachine` object with mapped NICs and disks | `oc get vm -n <target>` |

The conversion pod and the disk are visible in the target project while it runs:

```text
$ oc get dv,pvc,pods -n mtv-test -o wide
NAME                                                   PHASE       PROGRESS
datavolume.cdi.kubevirt.io/test-ubuntu-vm-2275-dm9s9   Succeeded   100.0%

NAME                                              STATUS   CAPACITY      ACCESS MODES   STORAGECLASS   VOLUMEMODE
persistentvolumeclaim/test-ubuntu-vm-2275-dm9s9   Bound    11381663335   RWX            nfs-csi        Filesystem

NAME                            READY   STATUS    IP            NODE
pod/test-ubuntu-vm-2275-m2czj   1/1     Running   10.130.0.88   ocp-node-1
```

### 15.4 Warm migration and cutover

Warm migration is the production default for VMs that cannot be off for the whole copy.

1. Prerequisites: CBT on (section 13), VDDK image on the provider (section 12).
2. Create the plan with **Migration type: Warm** (`spec.type: warm`, or the older `spec.warm: true`).
3. **Start**. MTV copies the full disks while the VM runs, then takes a snapshot and copies changed blocks at regular intervals (precopy interval, 60 minutes by default, configurable on the ForkliftController as `controller_precopy_interval`).
4. At the change window, click **Cutover** and choose now or a time. MTV shuts the source VM down through VMware Tools, copies the last delta, converts and starts the VM on OpenShift.

**CLI:** the cutover is a time on the Migration object:

```bash
# start warm migration with a scheduled cutover
cat <<'Y' | oc apply -f -
apiVersion: forklift.konveyor.io/v1beta1
kind: Migration
metadata: {name: wave01-app-web-1, namespace: openshift-mtv}
spec:
  plan: {name: wave01-app-web, namespace: openshift-mtv}
  cutover: "2026-10-10T22:00:00Z"
Y
# move the cutover to now
oc patch migration wave01-app-web-1 -n openshift-mtv --type merge \
  -p "{\"spec\":{\"cutover\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}}"
```

> **SCREENSHOT S-23:** Warm plan showing precopy iterations and the Cutover button.

### 15.5 Validate the migrated VM

1. **Virtualization > VirtualMachines**, project of the plan. MTV makes names DNS-safe (dots become dashes, lower case).
2. **Overview**: status Running, node, IP addresses (the guest agent reports them).
3. **Console**: log in, check `ip a`, routes and DNS against the baseline (section 13.4).
4. **Network interfaces** tab: NIC on `default/vlan-110` with the original MAC.
5. **Disks** tab: each disk on the expected StorageClass.
6. Application owner runs the smoke test and signs the baseline record.
7. **Live migrate** once (Actions > Migrate) to prove the VM is not pinned to one node.

> **SCREENSHOT S-24:** VirtualMachine Overview of the migrated VM, Running, with its IP and node.
>
> **SCREENSHOT S-25:** VM Console tab logged in, showing `ip a` with the original IP.

**CLI:**

```bash
oc get vm,vmi -n <target> -o wide                 # Running, node, IP
oc get vmi <vm> -n <target> -o jsonpath='{range .status.interfaces[*]}{.name}{" "}{.mac}{" "}{.ipAddress}{"\n"}{end}'
virtctl console <vm> -n <target>                  # serial console (Ctrl+] to exit)
virtctl migrate <vm> -n <target>                  # live migration test
oc get vmim -n <target>                           # live migration status
```

`virtctl` is downloaded from the web console: **?** (help) > **Command Line Tools** > **virtctl**.

### 15.6 Roll back

MTV never deletes or changes the source VM, apart from powering it off at cutover (warm) and leaving its snapshots cleaned up. Rollback is therefore simple and fast:

1. Stop the OpenShift VM: **Actions > Stop**, or `virtctl stop <vm> -n <target>`. This frees the IP.
2. Power the source VM on in vSphere.
3. Confirm the application on VMware.
4. Record the reason, fix it, and run the plan again with a new Migration.

Keep source VMs **powered off, not deleted**, until the agreed hypercare period ends (for example 14 days). Then delete them in vSphere and remove the MTV plan.

### Check yourself

**Q1. Which object do you change to move a scheduled cutover earlier?**
Answer: the **Migration** object's `spec.cutover`. Logic: the Plan says what to migrate; the Migration is the run, and the cutover time belongs to the run.

**Q2. A plan is not Ready and says a network is not mapped. The NetworkMap has the VM's only port group. Why?**
Answer: some VM in the plan has a second NIC (often a disconnected one) on another port group. Every NIC's port group needs a row. Remove the unused NIC in vSphere or add a mapping.

**Q3. Cold migration of a 500 GB VM without VDDK took 9 hours. What two changes cut the downtime the most?**
Answer: add the VDDK image (fast NBD/NFC reads from ESXi instead of HTTPS through vCenter), and switch to warm migration so the bulk copy happens while the VM runs and downtime is only the final delta.

**Q4. After migration the VM boots but shows a new DHCP address instead of its static IP. Why?**
Answer: either `preserveStaticIPs` could not read the guest's IP configuration (VMware Tools not running at migration time), or the NIC was mapped to the pod network. Check the plan's VM concerns and the NetworkMap.

## 16. Worked example: `ubuntu-noble-24.04-cloudimg` in the lab

This is the exact run captured for this SOP on 5 October 2026.

| Item | Value |
|---|---|
| Source | `ubuntu-noble-24.04-cloudimg`, powered off, 2 vCPU, 1 GB RAM, one 10 GiB disk on `DATASTORE-01`, NIC on port group `VM-Network-110`, vSphere MoRef `vm-2275` |
| Target | project `mtv-test`, StorageClass `nfs-csi` (RWX Filesystem), network `default/vlan-110` |
| Type | Cold, no VDDK |
| Files | `migration/test-ubuntu/test-ubuntu-plan.yaml` (NetworkMap + StorageMap + Plan), `test-ubuntu-migration.yaml` (Migration), `add-cloudinit-login.sh` |

### 16.1 Commands

```bash
oc new-project mtv-test
oc apply -f test-ubuntu-plan.yaml
oc get plan test-ubuntu -n openshift-mtv          # wait for READY True
oc apply -f test-ubuntu-migration.yaml            # = Start button
```

### 16.2 Timeline (real)

| Step | Started (UTC) | Completed (UTC) | Duration |
|---|---|---|---|
| Initialize | 15:08:05 | 15:08:15 | 10 s |
| DiskAllocation (10240 MB) | 15:08:15 | 15:08:21 | 6 s |
| ImageConversion | 15:08:21 | 15:31:47 | 23 min 26 s |
| DiskTransferV2v | 15:31:47 | running at time of writing (1740 of 10240 MB at 15:36) | |
| VirtualMachineCreation | | | |

**Lesson recorded:** without VDDK, virt-v2v reads the source disk over HTTPS through vCenter (`nbdkit curl`). For 10 GiB that is acceptable in a lab; for production disk sizes it is not. Section 12 is mandatory for production.

### 16.3 After it finishes

```bash
~/ocp-install/mtv-test/add-cloudinit-login.sh     # the cloud image has no password; adds a cloud-init login
```

Then **Virtualization > VirtualMachines > mtv-test**, start the VM, open **Console**, log in as `ubuntu`, run `ip a` and confirm the NIC is on VLAN 110. Follow section 15.5 for the full validation, and section 15.6 to clean up.

```bash
# clean up when done
oc delete project mtv-test
oc delete plan/test-ubuntu -n openshift-mtv     # keep the maps for the next VM
```

# Part D: Production

## 17. Production storage: FC, iSCSI and NFS

The lab uses one NFS export and a community driver. Production has Fibre Channel (FC), iSCSI and NFS arrays. The migration process is the same; what changes is **how the nodes reach the storage**, **which CSI driver** creates the volumes, **which StorageClass** each VMware datastore maps to, and the **volume mode** of the migrated disks.

### 17.1 Decision table

| | Fibre Channel (FC) | iSCSI | NFS |
|---|---|---|---|
| Protocol | SCSI over the FC fabric | SCSI over IP (TCP 3260) | File protocol over IP (TCP 2049) |
| VMware equivalent | VMFS datastore on FC LUNs | VMFS datastore on iSCSI LUNs | NFS datastore |
| What OpenShift gets | A LUN per PVC | A LUN per PVC | A directory (or volume) per PVC |
| CSI driver | Array vendor's certified CSI driver with FC support | Array vendor's certified CSI driver with iSCSI support | Array vendor's certified CSI driver with NFS support |
| Volume mode for VM disks | **Block** | **Block** | **Filesystem** |
| Access mode for live migration | RWX Block (the same LUN mapped to every node) | RWX Block | RWX Filesystem |
| Node prerequisites | HBAs, zoning to node WWPNs, multipath | Initiator IQN per node, `iscsid`, dedicated storage NICs/VLANs, multipath | Network path to the NFS LIFs, NFS v4.1 or v3 per vendor |
| Performance | Highest, lowest latency | High; depends on network, use jumbo frames | Good; extra filesystem layer |
| Snapshots / clones | Array-side via VolumeSnapshotClass (CSI) | Same | Same |
| Typical drivers | Dell CSM (PowerMax, PowerStore, Unity), Pure CSI / Portworx, HPE CSI, IBM Block CSI, Hitachi HSPC, NetApp Trident `ontap-san` (`sanType: fcp`) | Same vendors with iSCSI; NetApp Trident `ontap-san` (`sanType: iscsi`) | NetApp Trident `ontap-nas`, Dell PowerScale CSI, Pure FlashBlade |

**Rules that apply to all three:**

1. Use a CSI driver that is **certified for OpenShift and for OpenShift Virtualization** (Red Hat Ecosystem Catalog). The community NFS driver used in the lab is not supported.
2. VM disks should be **RWX** so VMs can live-migrate during node maintenance and upgrades. Confirm the driver supports RWX for the volume mode you choose (RWX Block for FC and iSCSI).
3. Check the driver's **StorageProfile** after install. If the driver is known to CDI it fills in the access and volume modes for you; if not, set them like the lab did (section 8.2).
4. Install the driver's **VolumeSnapshotClass** so VM snapshots and fast clones work.

### 17.2 FC: node preparation and StorageClass

**Concept.** FC nodes need their HBA ports zoned to the array, the node's WWPNs added to a host group on the array (most CSI drivers do this for you), and **multipath** so that losing one fabric does not lose the disk.

**Step 1: collect WWPNs from every worker** (give them to the SAN team for zoning):

```bash
for n in $(oc get nodes -l node-role.kubernetes.io/worker -o name); do
  echo "== $n"; oc debug $n -q -- chroot /host sh -c 'cat /sys/class/fc_host/host*/port_name'
done
```

**Step 2: enable multipath with a MachineConfig** (the only supported way to change node OS files on RHCOS). Use the multipath settings your array vendor publishes for RHEL 9:

```yaml
apiVersion: machineconfiguration.openshift.io/v1
kind: MachineConfig
metadata:
  name: 99-worker-multipath
  labels: {machineconfiguration.openshift.io/role: worker}
spec:
  config:
    ignition: {version: 3.4.0}
    storage:
      files:
      - path: /etc/multipath.conf
        mode: 0644
        overwrite: true
        contents:
          source: data:text/plain;charset=utf-8;base64,<BASE64_OF_VENDOR_MULTIPATH_CONF>
    systemd:
      units:
      - name: multipathd.service
        enabled: true
```

```bash
base64 -w0 multipath.conf          # paste the result into the source line above
oc apply -f 99-worker-multipath.yaml
oc get mcp worker -w               # nodes reboot one at a time; wait for UPDATED True
```

> **Why this reboots nodes:** MachineConfig changes are applied by the Machine Config Operator, which drains and reboots each node in turn. Running VMs live-migrate away first if their disks are RWX. Do this before the migration waves start, not during them.

**Step 3: install the vendor CSI driver** from the Software Catalog (most are certified operators) and create its backend/secret as the vendor documents.

**Step 4: StorageClass.** Example for NetApp Trident on FC (replace with your vendor's provisioner and parameters):

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: fc-gold
  annotations:
    storageclass.kubevirt.io/is-default-virt-class: "true"   # default class for VM disks
provisioner: csi.trident.netapp.io
parameters:
  backendType: ontap-san
  sanType: fcp
  fsType: ""                       # raw block for VM disks
allowVolumeExpansion: true
reclaimPolicy: Delete
volumeBindingMode: Immediate
```

**Verify:**

```bash
oc get storageprofile fc-gold -o jsonpath='{.status.claimPropertySets}{"\n"}'
# expect: [{"accessModes":["ReadWriteMany"],"volumeMode":"Block"}]
oc debug node/<worker> -q -- chroot /host multipath -ll     # each LUN shows 2+ active paths
```

### 17.3 iSCSI: node preparation and StorageClass

**Concept.** Each node is an iSCSI initiator with a unique IQN. Storage traffic should run on dedicated NICs or VLANs, ideally two subnets to two array controllers, with jumbo frames end to end and multipath on top.

**Step 1: initiator names and the `iscsid` service.** RHCOS generates a unique `/etc/iscsi/initiatorname.iscsi` per node. Collect them for the array host group if your driver does not register them itself:

```bash
for n in $(oc get nodes -l node-role.kubernetes.io/worker -o name); do
  echo "== $n"; oc debug $n -q -- chroot /host cat /etc/iscsi/initiatorname.iscsi
done
```

Enable `iscsid` and `multipathd` with a MachineConfig (same pattern as 17.2, adding a unit `iscsid.service` with `enabled: true`).

**Step 2: storage network with NMState.** Example: two storage VLANs on a bond, each with a node IP and MTU 9000. One policy per node because the IPs differ (or use a nodeSelector per hostname):

```yaml
apiVersion: nmstate.io/v1
kind: NodeNetworkConfigurationPolicy
metadata: {name: iscsi-worker-1}
spec:
  nodeSelector: {kubernetes.io/hostname: worker-1}
  desiredState:
    interfaces:
    - name: bond1.301
      type: vlan
      state: up
      mtu: 9000
      vlan: {base-iface: bond1, id: 301}
      ipv4: {enabled: true, dhcp: false, address: [{ip: 172.16.31.11, prefix-length: 24}]}
    - name: bond1.302
      type: vlan
      state: up
      mtu: 9000
      vlan: {base-iface: bond1, id: 302}
      ipv4: {enabled: true, dhcp: false, address: [{ip: 172.16.32.11, prefix-length: 24}]}
```

Test from the node: `oc debug node/worker-1 -- chroot /host ping -M do -s 8972 <array_iscsi_ip>` proves jumbo frames work without fragmentation.

**Step 3: StorageClass.** Example for NetApp Trident on iSCSI:

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata: {name: iscsi-silver}
provisioner: csi.trident.netapp.io
parameters:
  backendType: ontap-san
  sanType: iscsi
  fsType: ""
allowVolumeExpansion: true
reclaimPolicy: Delete
volumeBindingMode: Immediate
```

**Verify:** `iscsiadm -m session` on a node (through `oc debug`) shows sessions to every array portal, and `multipath -ll` shows two or more paths per LUN.

### 17.4 NFS: StorageClass

**Concept.** Nearly the lab setup, but with the vendor's CSI driver, export policies that allow the node subnet with root access for the driver, and NFS v4.1 (or the version the vendor recommends).

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata: {name: nfs-bronze}
provisioner: csi.trident.netapp.io
parameters:
  backendType: ontap-nas
mountOptions: ["nfsvers=4.1"]
allowVolumeExpansion: true
reclaimPolicy: Delete
volumeBindingMode: Immediate
```

**Verify:** the StorageProfile should show `ReadWriteMany` / `Filesystem`. Remember the filesystem overhead: a 100 GiB VMDK becomes a PVC of about 105.5 GiB on a Filesystem class (lab evidence: 10 GiB disk, 11.38 GB PVC).

### 17.5 What changes in the StorageMap and the Plan

In VMware, each datastore sits on one array and one protocol. Map **each datastore to the class on the same tier and protocol**, so the application keeps its performance tier:

```yaml
apiVersion: forklift.konveyor.io/v1beta1
kind: StorageMap
metadata: {name: prod-vcenter-storage, namespace: openshift-mtv}
spec:
  provider:
    source: {name: <PROD_VCENTER_PROVIDER>, namespace: openshift-mtv}
    destination: {name: host, namespace: openshift-mtv}
  map:
  - source: {name: DS-FC-GOLD-01}
    destination: {storageClass: fc-gold, accessMode: ReadWriteMany, volumeMode: Block}
  - source: {name: DS-FC-GOLD-02}
    destination: {storageClass: fc-gold, accessMode: ReadWriteMany, volumeMode: Block}
  - source: {name: DS-ISCSI-SILVER-01}
    destination: {storageClass: iscsi-silver, accessMode: ReadWriteMany, volumeMode: Block}
  - source: {name: DS-NFS-BRONZE-01}
    destination: {storageClass: nfs-bronze, accessMode: ReadWriteMany, volumeMode: Filesystem}
```

| Plan area | Lab (NFS) | Production FC / iSCSI | Production NFS |
|---|---|---|---|
| StorageMap destination | `nfs-csi`, RWX, Filesystem | `fc-*` / `iscsi-*`, RWX, **Block** | `nfs-*`, RWX, Filesystem |
| Capacity to request from the storage team | Disk size + 5.5% | Disk size (no filesystem overhead on Block) | Disk size + 5.5% |
| Thin provisioning | Thin by nature on NFS | Array thin provisioning; check the array's space reclamation (UNMAP) | Thin |
| Transfer speed | Limited by the NFS server | Fastest; consider storage copy offload (17.6) | Good |
| Shared disks / Windows clusters | Not suitable | RWX Block shared disk with `scsiReservation` | Not suitable |
| Plan settings to review | defaults | `transferNetwork`, `convertorNodeSelector`, `migrateSharedDisks`, `scsiReservation` | `transferNetwork` |

The Plan itself does not name a StorageClass. It references the StorageMap, so **the per-datastore choice is made once in the map** and every plan that uses that map follows it.

### 17.6 Advanced: storage copy offload, RDMs and shared disks

**Storage copy offload (XCOPY).** When the VMware datastore and the OpenShift StorageClass are on the **same array**, MTV can ask the array to copy the data internally instead of streaming it through the network. MTV 2.12 in the lab exposes this:

```text
$ oc api-resources --api-group=forklift.konveyor.io | grep -i xcopy
vspherexcopyvolumepopulators   vxvp,vxvps   forklift.konveyor.io/v1beta1   true   VSphereXcopyVolumePopulator

$ oc explain storagemap.spec.map.offloadPlugin --recursive
FIELDS:
  csiVolumeImport       <Object>
    secretRef           <string> -required-
    storageVendorProduct        <string> -required-
    enum: primera3par
  vsphereXcopyConfig    <Object>
    secretRef           <string> -required-
    storageVendorProduct        <string> -required-
    enum: flashsystem, vantara, ontap, primera3par, ....
```

It is configured per StorageMap row with a secret holding the array credentials:

```yaml
  - source: {name: DS-FC-GOLD-01}
    destination: {storageClass: fc-gold, volumeMode: Block, accessMode: ReadWriteMany}
    offloadPlugin:
      vsphereXcopyConfig:
        secretRef: array-gold-credentials         # Secret in openshift-mtv, keys per vendor docs
        storageVendorProduct: ontap               # one of the enum values above
```

Check the MTV documentation for your vendor's secret keys and supported protocols before using it. It can cut multi-terabyte copies from hours to minutes.

**RDM disks.** Two choices: Storage vMotion the RDM to a virtual disk before migration (simplest), or present the same LUN to the OpenShift nodes and set `rdmAsLun: true` on the plan so the VM gets the LUN passed through.

**Shared disks (Windows Server Failover Clusters, Oracle RAC).** Migrate all members together, let only one plan copy the shared disk (`migrateSharedDisks: true` on that plan, `false` on the others), use an RWX Block class, and set `scsiReservation: true` for SCSI-3 persistent reservations.

### 17.7 Production networking for migration traffic

By default, disk data flows over the pod network. For large waves create a dedicated **transfer network** (a NAD on a migration VLAN that can reach the ESXi management or NFC interfaces) and set it on the plan:

```yaml
spec:
  transferNetwork: {name: migration-vlan-250, namespace: openshift-mtv}
```

This keeps bulk copy traffic off the cluster's default network and lets the network team give it its own QoS.

### Check yourself

**Q1. Why Block volume mode for FC and iSCSI but Filesystem for NFS?**
Answer: FC and iSCSI deliver raw LUNs. Handing the LUN straight to the VM as a block device avoids an extra filesystem layer and its 5.5% overhead, and gives the best latency. NFS delivers files, so the VM disk has to be a file on the share (Filesystem mode).

**Q2. A VM on an FC LUN cannot live-migrate. The PVC shows RWO. What do you change?**
Answer: the StorageMap destination (and the StorageProfile defaults) to `accessMode: ReadWriteMany`, `volumeMode: Block`, provided the CSI driver supports RWX Block. Existing PVCs cannot change access mode; migrate the VM again or clone the disk to a new RWX PVC.

**Q3. After applying the multipath MachineConfig, a node is stuck `SchedulingDisabled`. What is happening?**
Answer: the Machine Config Operator is draining it before reboot and a pod or VM cannot be evicted (often a VM with an RWO disk, or a PodDisruptionBudget). Check `oc get vmi -A -o wide` on that node and `oc describe node`; live-migrate or stop the blocking VM.

**Q4. You have the same NetApp array behind both vSphere and OpenShift. What can make migration much faster?**
Answer: storage copy offload (`vsphereXcopyConfig` with `storageVendorProduct: ontap`), so the array copies the blocks internally.

## 18. Wave planning and change runbook

### 18.1 Wave planning

| Wave | Content | Purpose |
|---|---|---|
| 0 | Test VMs (like the lab Ubuntu VM), one Linux and one Windows, one per storage protocol | Prove each protocol, network and the runbook |
| 1 | Low-risk, stateless VMs (web, jump hosts) | Build confidence and timing data |
| 2..n | Application groups, all tiers of one application together | Keep dependencies intact |
| Last | Databases, clusters with shared disks, large VMs | Most planning, warm migration, longest windows |

Group VMs by **application**, not by datastore. Keep a plan to 10 to 20 VMs so a failure is easy to isolate. Use MTV's measured throughput from earlier waves to size the window: `window ≥ (total GB to copy ÷ measured GB per hour) + conversion + validation + rollback buffer`.

### 18.2 Runbook (per wave)

| Step | When | Action | Owner | Done |
|---|---|---|---|---|
| R1 | T-5 days | Change approved; application owners and rollback agreed | Change manager | |
| R2 | T-5 days | Section 13 checks on every VM; baseline records filled | Migration engineer | |
| R3 | T-3 days | Backups of source VMs verified | Backup team | |
| R4 | T-2 days | Plan created and Ready; all Critical concerns cleared | Migration engineer | |
| R5 | T-2 days | Warm plans started (precopy running) | Migration engineer | |
| R6 | T-1 hour | Section 7 health checks; storage capacity checked | Platform team | |
| R7 | T-0 | Application owner stops the service (if needed); cutover / Start | Migration engineer | |
| R8 | T+ | Watch the pipeline (section 15.3) | Migration engineer | |
| R9 | T+ | Validate each VM (section 15.5); application smoke tests | App owner | |
| R10 | T+ | Go / no-go. No-go: rollback (section 15.6) | Change manager | |
| R11 | T+1 day | Update CMDB, monitoring, backup jobs for the new VMs | Platform team | |
| R12 | T+14 days | Hypercare ends; delete source VMs; archive the plan | Migration engineer | |

### 18.3 Sign-off record

| VM | Plan | Migration | Start | End | Downtime | Validated by | Result |
|---|---|---|---|---|---|---|---|
| ubuntu-noble-24.04-cloudimg | test-ubuntu | test-ubuntu-1 | 15:08 UTC | | n/a (cold, test) | | |

## 19. Troubleshooting

| Symptom | Likely cause | How to confirm | Fix |
|---|---|---|---|
| Provider not Ready, `ConnectionTestSucceeded` False | Wrong URL/credentials, DNS, port 443 blocked, cert | `oc get provider <p> -n openshift-mtv -o yaml` conditions | Fix secret or URL; `curl -kI https://<vcenter>/sdk` from a node |
| Provider Ready, inventory empty | Account lacks permission on the VMs' folders | Log in to vSphere as the MTV account | Propagate the role (section 11) |
| Plan not Ready: "network not mapped" | A VM NIC on an unmapped port group | Plan concerns | Add a row to the NetworkMap |
| Plan not Ready: "storage not mapped" | A VM disk on an unmapped datastore | Plan concerns | Add a row to the StorageMap |
| Stuck in Initialize, pod `ImagePullBackOff` | VDDK image path wrong or not pullable | `oc get pods -n <target>`; `oc describe pod` | Fix `vddkInitImage`; check registry access |
| DiskTransfer very slow | No VDDK (HTTPS fallback), slow network or storage | Pod log shows `nbdkit curl` | Add VDDK; use a transfer network; check array |
| DiskTransfer fails at the start | Ports 443/902 to ESXi blocked, ESXi name not resolvable | Importer/v2v pod log | Open the firewall; fix DNS for ESXi hosts |
| PVC `Pending` | StorageClass missing, driver down, array full, wrong access mode | `oc describe pvc` | Fix the class or capacity; check the CSI pods |
| ImageConversion fails | Unsupported guest, BitLocker, dirty NTFS (Fast Startup), missing kernel modules | `oc logs <v2v pod>` near the end | Fix the guest (section 13.3) and run again |
| VM boots to a disk error | Boot disk order, UEFI vs BIOS mismatch | VM console | Check firmware in the VM spec matches the source |
| VM running, no network | Mapped to pod network, wrong VLAN on NAD, trunk missing on switch or port group | `oc get vmi -o yaml` interfaces; NNCP status | Fix the map/NAD/trunk |
| VM cannot live-migrate | RWO disk, or no node has the bridge | `oc get vmi <vm> -o jsonpath='{.status.conditions}'` (LiveMigratable) | Use RWX class; apply NNCP to all workers |
| Warm migration never reaches cutover | CBT not enabled or snapshot failures | Plan VM pipeline, vSphere tasks | Enable CBT, remove old snapshots |

**Logs to collect for a support case:** `oc adm must-gather --image=registry.redhat.io/migration-toolkit-virtualization/mtv-must-gather-rhel8:<MTV_VERSION>` (check the exact image name in the MTV documentation for your version), plus the plan and migration YAML.

## 20. Review questions (exam style)

These follow the style of the Red Hat OpenShift Virtualization exam (EX316) and the administration exams (EX280). Try each one on the lab before reading the answer.

**Q1. Create a project `vm-prod` and a NAD there that puts VMs on VLAN 120 through bridge `br-vm`.**
Answer:
```bash
oc new-project vm-prod
cat <<'Y' | oc apply -f -
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata: {name: vlan-120, namespace: vm-prod}
spec:
  config: '{"cniVersion":"0.3.1","name":"vlan-120","type":"bridge","bridge":"br-vm","vlan":120,"macspoofchk":true,"ipam":{}}'
Y
```
Logic: the bridge exists on every node (NNCP); the NAD only adds the VLAN tag and the name VMs refer to. A NAD in `vm-prod` is usable only by VMs in that project.

**Q2. Which three objects must exist before a Plan can be Ready?**
Answer: a Ready source Provider, a NetworkMap and a StorageMap that cover every NIC and disk of every VM in the plan (plus the destination provider `host`, which exists automatically).

**Q3. Make `nfs-csi` no longer the default StorageClass and make `fc-gold` the default for VMs only.**
Answer:
```bash
oc patch sc nfs-csi -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"false"}}}'
oc patch sc fc-gold -p '{"metadata":{"annotations":{"storageclass.kubevirt.io/is-default-virt-class":"true"}}}'
```
Logic: OpenShift Virtualization prefers the `is-default-virt-class` class for VM disks, while pods keep using the cluster default.

**Q4. A warm migration's cutover is set for 22:00. The application team is ready at 20:00. Do it now with the CLI.**
Answer: `oc patch migration <name> -n openshift-mtv --type merge -p "{\"spec\":{\"cutover\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}}"`. Logic: cutover is a timestamp on the Migration; setting it to now triggers the final sync.

**Q5. Show the pipeline step and progress of every VM in plan `wave01`.**
Answer: `oc get plan wave01 -n openshift-mtv -o jsonpath='{range .status.migration.vms[*]}{.name}{": "}{.phase}{"\n"}{end}'` for the VM phase, or the pipeline query in section 15.3 for each step.

**Q6. Why is a Plan separate from a Migration?**
Answer: the Plan is the reusable description; each Migration is one execution with its own history. A failed run is retried by creating a new Migration without touching the plan.

**Q7. The NNCP is Degraded and one NNCE says the interface `ens34` does not exist on `worker-3`. What are two fixes?**
Answer: add the missing NIC (or fix its name) on that node, or restrict the policy with a nodeSelector to the nodes that have it. If only some nodes have the bridge, VMs using it must be kept on those nodes (the NAD `resourceName` annotation handles scheduling).

**Q8. A VM was migrated with an RWO disk. Can you make it live-migratable without migrating it again?**
Answer: yes, by copying the disk to a new RWX PVC (for example with a DataVolume clone or a storage migration if your version supports it), then pointing the VM at the new PVC while it is stopped. Access mode of an existing PVC cannot change in place.

**Q9. Which plan option keeps the guest's static IP after migration, and what does it depend on?**
Answer: `preserveStaticIPs: true`. It depends on VMware Tools reporting the guest IP configuration to vCenter during migration, and on the NIC being mapped to a bridged network on the same VLAN.

**Q10. List the files you would hand to a colleague to repeat the lab migration from scratch.**
Answer: `install/install-cnv-nmstate-mtv.sh`, `install/install-nfs-csi.sh`, `install/vm-networks.sh`, `install/add-mtv-vsphere-provider.sh`, `migration/test-ubuntu/test-ubuntu-plan.yaml`, `migration/test-ubuntu/test-ubuntu-migration.yaml`, and this SOP.

## Appendix A: Command cheat sheet

| Task | Command |
|---|---|
| Cluster health | `oc get clusterversion; oc get nodes; oc get co` |
| Operators | `oc get csv -n openshift-cnv; oc get csv -n openshift-mtv` |
| Storage | `oc get sc; oc get storageprofile; oc get pvc -A` |
| VM networks | `oc get nncp; oc get nnce; oc get net-attach-def -A` |
| MTV objects | `oc get providers,networkmaps,storagemaps,plans,migrations -n openshift-mtv` |
| Plan progress | `oc get plan <p> -n openshift-mtv -o jsonpath='{range .status.migration.vms[*].pipeline[*]}{.name}{"\t"}{.phase}{"\t"}{.progress.completed}/{.progress.total}{"\n"}{end}'` |
| Migration pods | `oc get pods -n <target>; oc logs <pod> -n <target>` |
| VMs | `oc get vm,vmi -n <target> -o wide` |
| Start / stop / console | `virtctl start <vm>; virtctl stop <vm>; virtctl console <vm>` |
| Live migrate | `virtctl migrate <vm> -n <target>; oc get vmim -n <target>` |
| Explain any field | `oc explain plan.spec --recursive`, `oc explain storagemap.spec.map` |

## Appendix B: Files in this project

| File | Purpose |
|---|---|
| `install/install-config.yaml`, `install/agent-config.yaml`, `install/build-agent-iso.sh` | Cluster install (agent-based installer) |
| `install/install-nfs-csi.sh` | NFS CSI driver and `nfs-csi` StorageClass |
| `install/install-cnv-nmstate-mtv.sh` | OpenShift Virtualization, NMState and MTV operators |
| `install/vm-networks.sh` | NNCP `br-vm-ens34` and the three NADs |
| `install/add-htpasswd-admin.sh` | Local admin user `ocpadmin` |
| `install/add-mtv-vsphere-provider.sh` | vSphere provider `vcenter-lab` |
| `migration/scripts/New-MtvVcenterAccount.ps1` | vCenter role and service account for MTV |
| `migration/test-ubuntu/*` | Worked example plan, migration and cloud-init login |
| `sop/src/*.md` | This SOP (master copy); `sop/build.py` builds HTML and DOCX |
