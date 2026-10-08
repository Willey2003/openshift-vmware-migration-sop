# Chapter 5 · Storage: NFS, CSI and Persistent Volumes

A VM is mostly its disks. On vSphere a disk is a VMDK file on a datastore. On OpenShift a VM disk is a **PersistentVolumeClaim (PVC)**: a request for storage that a **StorageClass** fulfils by creating a **PersistentVolume (PV)** through a **CSI driver**. Learn these four words and the rest of the book is easy.

| OPENSHIFT | VSPHERE | IN THIS LAB |
|---|---|---|
| StorageClass | A storage policy (SPBM) + the datastore it lands on | `nfs-csi` |
| CSI driver | The storage vendor's VASA provider / plug-in | `nfs.csi.k8s.io` (csi-driver-nfs v4.11.0) |
| PersistentVolume (PV) | The VMDK file | A folder on `/exports/ocp` |
| PersistentVolumeClaim (PVC) | The VM's "Hard disk 1" entry pointing at that VMDK | `mtv-test/test-ubuntu-vm-2275-dm9s9` |
| Access mode RWX | A shared datastore every host can open | Needed for live migration |
| Access mode RWO | A disk only one host can open at a time | Block arrays without RWX support |

> **NOTE** **RWX (ReadWriteMany)** is the property that makes VM live migration possible: while a VM moves, the source and target nodes both have its disk open for a moment. It is the same reason vMotion without Storage vMotion needs a datastore shared by both hosts.

## The NFS server (bastion side)

**admin@bastion — real capture**

```console
[admin@bastion ~]$ sudo exportfs -v; showmount -e localhost
/exports/ocp  	198.51.100.0/24(sync,no_wdelay,hide,no_subtree_check,sec=sys,rw,secure,no_root_squash,no_all_squash)
Export list for localhost:
/exports/ocp 198.51.100.0/24
```

| OPTION | MEANING | WHY HERE |
|---|---|---|
| `198.51.100.0/24` | Who may mount | Covers the node VLAN (.32/27) and the bastion |
| `rw` | Read-write | VMs write to their disks |
| `sync` | Reply only after data is on disk | Safety for VM disks |
| `no_root_squash` | Root on the client stays root on the share | The CSI driver creates folders and sets ownership as root |

```bash
# Reference: how the export was created
echo '/exports/ocp 198.51.100.0/24(rw,sync,no_root_squash,no_subtree_check)' | sudo tee /etc/exports.d/ocp.exports
sudo systemctl enable --now nfs-server
sudo firewall-cmd --permanent --add-service={nfs,mountd,rpc-bind} && sudo firewall-cmd --reload
sudo exportfs -ra
```

## The CSI driver and StorageClass (cluster side)

```bash
# Reference: ~/ocp-install/install-nfs-csi.sh (key lines)
V=v4.11.0; B=https://raw.githubusercontent.com/kubernetes-csi/csi-driver-nfs/$V/deploy
oc apply -f $B/rbac-csi-nfs.yaml -f $B/csi-nfs-driverinfo.yaml
oc adm policy add-scc-to-user privileged -z csi-nfs-controller-sa -n kube-system
oc adm policy add-scc-to-user privileged -z csi-nfs-node-sa -n kube-system
oc apply -f $B/csi-nfs-controller.yaml -f $B/csi-nfs-node.yaml
oc apply -f sc-nfs-csi.yaml
```

> **NOTE** The two `add-scc-to-user privileged` lines are the OpenShift-specific part. A CSI node plug-in must mount filesystems on the host, which the default `restricted-v2` **Security Context Constraint** forbids. Upstream Kubernetes manifests do not know about SCCs, so on OpenShift you grant the driver's service accounts the `privileged` SCC yourself.

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get sc; oc get csidriver
NAME                PROVISIONER      RECLAIMPOLICY   VOLUMEBINDINGMODE   ALLOWVOLUMEEXPANSION   AGE
nfs-csi (default)   nfs.csi.k8s.io   Delete          Immediate           true                   12h
NAME             ATTACHREQUIRED   PODINFOONMOUNT   STORAGECAPACITY   TOKENREQUESTS   REQUIRESREPUBLISH   MODES        AGE
nfs.csi.k8s.io   false            false            false             <unset>         false               Persistent   12h
```

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get sc nfs-csi -o yaml
allowVolumeExpansion: true
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
  name: nfs-csi
mountOptions:
- nfsvers=4.1
parameters:
  server: 198.51.100.24
  share: /exports/ocp
  subDir: ${pvc.metadata.namespace}-${pvc.metadata.name}-${pv.metadata.name}
provisioner: nfs.csi.k8s.io
reclaimPolicy: Delete
volumeBindingMode: Immediate
```

(Output trimmed: `uid`, `resourceVersion` and the last-applied annotation removed.)

| FIELD | MEANING |
|---|---|
| `is-default-class: "true"` | PVCs that name no StorageClass get this one |
| `server`, `share` | The NFS export from the previous section |
| `subDir` | Each PV is a folder named *namespace-pvc-pv*, so you can find any VM's disk on the bastion |
| `reclaimPolicy: Delete` | Deleting the PVC deletes the folder. **The disk is gone.** |
| `volumeBindingMode: Immediate` | Create the PV as soon as the PVC appears, without waiting for a Pod |
| `allowVolumeExpansion: true` | You can grow a VM disk by editing the PVC size |

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get pods -n kube-system -o wide | grep -E "NAME|csi-nfs"
NAME                                  READY   STATUS    RESTARTS     AGE   IP            NODE        NOMINATED NODE   READINESS GATES
csi-nfs-controller-849b678558-dj4jp   5/5     Running   1 (8h ago)   8h    198.51.100.35   ocp-node-2   <none>           <none>
csi-nfs-node-jhcnx                    3/3     Running   0            12h   198.51.100.36   ocp-node-3   <none>           <none>
csi-nfs-node-smmxz                    3/3     Running   0            12h   198.51.100.34   ocp-node-1   <none>           <none>
csi-nfs-node-z8tfd                    3/3     Running   0            12h   198.51.100.35   ocp-node-2   <none>           <none>
```

One **controller** (creates and deletes folders) and one **node** Pod per node (a DaemonSet: mounts shares for the Pods on that node). Their IPs are node IPs because they use host networking.

## Following a disk from PVC to folder

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get pv
NAME                                       CAPACITY      ACCESS MODES   RECLAIM POLICY   STATUS   CLAIM                                                             STORAGECLASS   VOLUMEATTRIBUTESCLASS   REASON   AGE
pvc-21cff29a-6a67-4a87-be8c-1aab4e24f7a7   34144990004   RWX            Delete           Bound    openshift-virtualization-os-images/centos-stream10-041e2e2ceec9   nfs-csi        <unset>                          12h
pvc-397a4657-b361-4c77-9370-52d6ef45892c   34144990004   RWX            Delete           Bound    openshift-virtualization-os-images/rhel10-c5b97492a6e3            nfs-csi        <unset>                          12h
pvc-7d28af8c-864e-49c5-81be-30a007400184   34144990004   RWX            Delete           Bound    openshift-virtualization-os-images/rhel9-7005186c23b8             nfs-csi        <unset>                          12h
pvc-97112ec8-28ce-4cb7-8936-e5f87cbdfbcc   34144990004   RWX            Delete           Bound    openshift-virtualization-os-images/fedora-1217dcc8c58d            nfs-csi        <unset>                          12h
pvc-b501b26f-c8c4-4b40-b827-cd00be663a28   34144990004   RWX            Delete           Bound    openshift-virtualization-os-images/centos-stream9-e5d76202b136    nfs-csi        <unset>                          12h
pvc-e18f93ca-a91e-46e9-9306-2bda130b4efd   11381663335   RWX            Delete           Bound    mtv-test/test-ubuntu-vm-2275-dm9s9                                nfs-csi        <unset>                          5h53m
pvc-e2adc166-a479-4558-a0b4-1cc1f3dcc299   34144990004   RWX            Delete           Bound    openshift-virtualization-os-images/rhel8-c6a08edc555b             nfs-csi        <unset>                          12h
```

**admin@bastion — real capture**

```console
[admin@bastion ~]$ sudo ls /exports/ocp | head -10
mtv-test-prime-eabb3341-7b20-4f01-8735-f6d440f95735-pvc-e18f93ca-a91e-46e9-9306-2bda130b4efd
openshift-virtualization-os-images-prime-741ce51b-0d56-4264-87c2-39060b16b6ca-pvc-7d28af8c-864e-49c5-81be-30a007400184
openshift-virtualization-os-images-prime-82e09ba6-2fc1-4442-9607-f453404b1530-pvc-b501b26f-c8c4-4b40-b827-cd00be663a28
openshift-virtualization-os-images-prime-8562c120-3d43-49a3-905d-486697b839e9-pvc-97112ec8-28ce-4cb7-8936-e5f87cbdfbcc
openshift-virtualization-os-images-prime-8f4e5e3c-65de-482d-92b3-1d76b43a696f-pvc-397a4657-b361-4c77-9370-52d6ef45892c
openshift-virtualization-os-images-prime-db63ec98-2b40-4e46-978e-0f1321a5d7a5-pvc-21cff29a-6a67-4a87-be8c-1aab4e24f7a7
openshift-virtualization-os-images-prime-fdc29b13-59e0-4737-ae09-ff13e2f17d40-pvc-e2adc166-a479-4558-a0b4-1cc1f3dcc299
```

Match the PV name to the folder: the migrated Ubuntu disk `pvc-e18f93ca-...` is the first folder. Its middle part says `prime-eabb3341-...` and not the PVC name: the disk was first written into a temporary "prime" PVC by the importer and then handed over (**rebound**) to the final PVC. That is how CDI (the Containerized Data Importer, Chapter 6) and MTV populate disks safely: the VM never sees a half-written disk.

> **TIP** `CAPACITY 11381663335` bytes is 10.6 GiB for a 10 GiB source disk. CDI adds room for filesystem overhead because on NFS the disk is a `disk.img` file inside a filesystem, not a raw block device.

> **GOTCHA — REAL ISSUE** OpenShift Virtualization could not guess the right access mode for an NFS StorageClass it had never seen, so new VM disks would have defaulted to RWO and lost live migration. The fix was to tell CDI about it through the **StorageProfile**:
> `oc patch storageprofile nfs-csi --type merge -p '{"spec":{"claimPropertySets":[{"accessModes":["ReadWriteMany"],"volumeMode":"Filesystem"}],"cloneStrategy":"copy"}}'`

> **PRODUCTION** A single NFS VM is a lab shortcut and a single point of failure. In production use the array's own CSI driver (NetApp Trident, Dell PowerStore/PowerMax CSI, Pure Portworx, IBM Block CSI, HPE CSI) or OpenShift Data Foundation, with **block** volumes (`volumeMode: Block`) and RWX where the array supports it (FC and iSCSI LUNs via the vendor CSI; NFS via Trident). Check that each StorageProfile reports RWX for live migration and supports snapshots for backup.

> **EXAM TIP** EX280: StorageClasses, PVCs, default class, access modes, expanding a PVC. EX316: why VM live migration needs RWX, StorageProfiles, DataVolumes.

> **LAB — DO IT ON THE BASTION** Create a 1 GiB PVC in a new project `storage-lab` with no StorageClass name, watch it bind (`oc get pvc -w`), find its folder under `/exports/ocp`, then delete the PVC and prove the folder disappears (`reclaimPolicy: Delete`). Delete the project and run `oc project default` afterwards (remember Chapter 4).

## Check yourself

**Q1. A PVC has been `Pending` for five minutes. Name three things to check, in order.**
`oc describe pvc` events; that a default StorageClass exists (`oc get sc`); that the CSI controller Pod is Running and can reach the NFS server. *Logic:* PVC events tell you which layer refused; the class decides which driver; the driver needs the backend.

**Q2. You delete a migrated VM's PVC by mistake. Can you get the disk back from the bastion?**
No, not with this StorageClass. *Logic:* `reclaimPolicy: Delete` makes the CSI driver delete the folder with the PV. Use `Retain` (or snapshots/backups) for data you cannot lose.

**Q3. Why does the CSI driver need the `privileged` SCC on OpenShift but not on plain Kubernetes?**
OpenShift's default SCC forbids host mounts and privileged containers. *Logic:* SCCs are an OpenShift admission layer that upstream manifests do not know about, so you grant it to the driver's service accounts explicitly.

**Q4. Why does a VM disk on NFS need RWX to live-migrate?**
During migration both the source and target virt-launcher Pods mount the same PVC at once. *Logic:* RWO allows one node; the target node could not attach the disk, so the migration is refused (`LiveMigratable=False`).
