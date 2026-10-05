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
