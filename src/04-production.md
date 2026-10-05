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
