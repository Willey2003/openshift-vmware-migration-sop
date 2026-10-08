# Chapter 6 · OpenShift Virtualization: VMs as Pods

OpenShift Virtualization is Red Hat's build of the upstream **KubeVirt** project. It runs a normal KVM virtual machine (the same hypervisor as RHEL and RHV) **inside a Pod**. The VM gets everything a Pod gets: scheduling, networking, storage, RBAC, monitoring, and the same `oc` commands.

| VSPHERE | OPENSHIFT VIRTUALIZATION | NOTE |
|---|---|---|
| ESXi host | Node with `/dev/kvm` | KVM is in the RHCOS kernel |
| VM (the `.vmx` definition) | `VirtualMachine` (VM) object | Exists even when powered off |
| Running VM process (`vmx`) | `VirtualMachineInstance` (VMI) + `virt-launcher-<vm>` Pod | Exists only while running |
| vMotion | `VirtualMachineInstanceMigration` (VMIM) | Live migration, needs RWX disk |
| VMDK | PVC / DataVolume | Chapter 5 |
| Port group | NetworkAttachmentDefinition (NAD) | Chapter 7 |
| VM template / content library | Instance types + preferences + boot sources (DataSources) | Below |
| VMware Tools | QEMU guest agent + virtio drivers | MTV installs them during conversion |
| vCenter | The OpenShift API + web console *Virtualization* section | |

## Install: three operators

```bash
# Reference: ~/ocp-install/install-cnv-nmstate-mtv.sh (key part)
sub openshift-cnv     kubevirt-hyperconverged     stable          # Namespace + OperatorGroup + Subscription
sub openshift-nmstate kubernetes-nmstate-operator stable
sub openshift-mtv     mtv-operator                release-v2.12
oc apply -f - <<'Y'
apiVersion: hco.kubevirt.io/v1beta1
kind: HyperConverged                      # "turn Virtualization on" with defaults
metadata: {name: kubevirt-hyperconverged, namespace: openshift-cnv}
spec: {}
---
apiVersion: nmstate.io/v1
kind: NMState                             # "turn NMState on"
metadata: {name: nmstate}
---
apiVersion: forklift.konveyor.io/v1beta1
kind: ForkliftController                  # "turn MTV on"
metadata: {name: forklift-controller, namespace: openshift-mtv}
spec: {olm_managed: true}
Y
```

An **Operator** is installed in two steps: a **Subscription** tells OLM (the Operator Lifecycle Manager) which package and channel to follow; OLM installs a **ClusterServiceVersion (CSV)**, the operator itself. Then you create the operator's own **custom resource** (HyperConverged, NMState, ForkliftController) to say "deploy it".

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get subscription -A
NAMESPACE           NAME                          PACKAGE                       SOURCE             CHANNEL
openshift-cnv       kubevirt-hyperconverged       kubevirt-hyperconverged       redhat-operators   stable
openshift-mtv       mtv-operator                  mtv-operator                  redhat-operators   release-v2.12
openshift-nmstate   kubernetes-nmstate-operator   kubernetes-nmstate-operator   redhat-operators   stable
```

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get csv -A | grep -vE "^openshift-(operator-lifecycle|operators )" | awk "!seen[\$2]++" | head -20
NAMESPACE                              NAME                                              DISPLAY                                         VERSION               RELEASE   REPLACES                                   PHASE
openshift-cnv                          kubevirt-hyperconverged-operator.v4.22.9          OpenShift Virtualization                        4.22.9                          kubevirt-hyperconverged-operator.v4.22.6   Succeeded
openshift-mtv                          mtv-operator.v2.12.9                              Migration Toolkit for Virtualization Operator   2.12.9                          mtv-operator.v2.12.8                       Succeeded
openshift-nmstate                      kubernetes-nmstate-operator.4.22.0-202609230131   Kubernetes NMState Operator                     4.22.0-202609230131                                                        Succeeded
```

`REPLACES ...v4.22.6` shows OLM already upgraded Virtualization from 4.22.6 to 4.22.9 by following the `stable` channel. `PHASE Succeeded` is the only healthy value.

> **SCREENSHOT S-04:** *Operators > Installed Operators*, all projects: OpenShift Virtualization 4.22.9, Migration Toolkit for Virtualization 2.12.9 and Kubernetes NMState Operator, each *Succeeded*.

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get hco -n openshift-cnv; oc get hco kubevirt-hyperconverged -n openshift-cnv -o jsonpath="{range .status.conditions[*]}{.type}={.status}{\"\n\"}{end}"
NAME                      AGE
kubevirt-hyperconverged   12h
ReconcileComplete=True
Available=True
Progressing=False
Degraded=False
Upgradeable=True
```

The **HyperConverged** (HCO) object is the one switch for all of Virtualization: KubeVirt, CDI (disk import), networking add-ons, SSP (templates), the console plug-in. `Available=True, Degraded=False` = healthy.

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get pods -n openshift-cnv | head -30
NAME                                                   READY   STATUS    RESTARTS      AGE
aaq-operator-575758df8d-n2qcs                          1/1     Running   0             8h
bridge-marker-m8dkr                                    1/1     Running   0             12h
...
cdi-apiserver-7c7d7d56f5-wq5g2                         1/1     Running   1 (12h ago)   12h
cdi-deployment-7cbbc6cbb-zxxtm                         1/1     Running   0             8h
cdi-operator-7bb8d69cdb-bdtb6                          1/1     Running   0             8h
cdi-uploadproxy-5bf887d9bc-xsxzt                       1/1     Running   0             8h
cluster-network-addons-operator-84c86f8c9f-9kxlm       1/1     Running   0             8h
hco-operator-65668c9cb-vvgw6                           1/1     Running   0             8h
...
kube-cni-linux-bridge-plugin-czck8                     1/1     Running   0             12h
...
kubemacpool-mac-controller-manager-799884cb8f-dqcw2    1/1     Running   1 (12h ago)   12h
...
kubevirt-console-plugin-6f89f54dbd-dtdmr               1/1     Running   0             12h
...
ssp-operator-7fcf697f6d-4d7p9                          1/1     Running   0             8h
virt-api-7c595746c7-5ptdw                              1/1     Running   0             12h
virt-api-7c595746c7-j7l7f                              1/1     Running   0             8h
```

| POD FAMILY | JOB | VSPHERE ANALOGY |
|---|---|---|
| `virt-api`, `virt-controller`, `virt-handler` (one per node) | Create, schedule and run VMs | vpxd + hostd |
| `cdi-*` | Import, upload and clone disks | Content library / OVF import |
| `bridge-marker`, `kube-cni-linux-bridge-plugin` | Attach VMs to linux bridges | vSwitch / VDS |
| `kubemacpool-*` | Hand out unique MAC addresses | vCenter MAC allocation |
| `ssp-operator` | Templates, instance types, boot sources | VM templates |
| `kubevirt-console-plugin` | The *Virtualization* pages in the web console | vSphere Client plug-in |

## Is hardware virtualization really there?

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get nodes -o custom-columns=NODE:.metadata.name,KVM:.status.allocatable.devices\\.kubevirt\\.io/kvm,CPU:.status.allocatable.cpu,MEM:.status.allocatable.memory
NODE        KVM   CPU      MEM
ocp-node-1   1k    11500m   48175496Ki
ocp-node-2   1k    11500m   48175492Ki
ocp-node-3   1k    11500m   48175504Ki
```

Every node advertises `devices.kubevirt.io/kvm: 1k` (1000 VM slots): `virt-handler` found `/dev/kvm`. A node without it would show `<none>` and no VM could be scheduled there. Allocatable CPU is 11.5 of 12 cores: OpenShift reserves the rest for the node itself.

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc debug node/ocp-node-1 --to-namespace=default -q -- chroot /host bash -c "ls -l /dev/kvm; grep -c -E \"vmx|svm\" /proc/cpuinfo"
crw-rw-rw-. 1 root kvm 10, 232 Oct  5 20:27 /dev/kvm
24
```

`oc debug node/...` starts a temporary privileged Pod on the node and `chroot /host` puts you in the node's real filesystem: the supported way to "SSH" into RHCOS. `/dev/kvm` exists, and 24 lines of `/proc/cpuinfo` carry the Intel `vmx` flag (two flag fields for each of the 12 vCPUs), proving vSphere's *Expose hardware assisted virtualization* is on.

> **GOTCHA — REAL ISSUE** Nested virtualization works, but it is slow and VMware does not support it for production. A nested VM's disk and CPU-heavy work (like the virt-v2v conversion in Chapter 9) run several times slower than on bare metal.

## Boot sources, instance types and preferences

Virtualization downloads ready-to-boot OS images and keeps them up to date. They are the new "golden templates".

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get datasource -n openshift-virtualization-os-images
NAME              AGE
centos-stream10   12h
centos-stream9    12h
fedora            12h
rhel10            12h
rhel7             12h
rhel8             12h
rhel9             12h
win10             12h
win11             12h
win2k16           12h
win2k19           12h
win2k22           12h
win2k25           12h
```

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get dv -A | head -15
NAMESPACE                            NAME                           PHASE       PROGRESS   RESTARTS   AGE
openshift-virtualization-os-images   centos-stream10-041e2e2ceec9   Succeeded   100.0%                12h
openshift-virtualization-os-images   centos-stream9-e5d76202b136    Succeeded   100.0%                12h
openshift-virtualization-os-images   fedora-1217dcc8c58d            Succeeded   100.0%                12h
openshift-virtualization-os-images   rhel10-c5b97492a6e3            Succeeded   100.0%                12h
openshift-virtualization-os-images   rhel8-c6a08edc555b             Succeeded   100.0%                12h
openshift-virtualization-os-images   rhel9-7005186c23b8             Succeeded   100.0%                12h
```

Thirteen **DataSources** exist but only six have a **DataVolume** behind them: the six Linux images Red Hat can ship automatically. `rhel7` and all Windows entries are empty pointers, because Microsoft licensing means you upload your own Windows image. A **DataVolume (DV)** is "a PVC plus instructions for filling it" (import from a URL or registry, upload, or clone); CDI does the work and the DV reports `PHASE` and `PROGRESS`.

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get virtualmachineclusterinstancetype | head -12; echo; oc get virtualmachineclusterpreference | grep -E "NAME|rhel|ubuntu|windows.2k22|fedora"
NAME             AGE
cx1.2xlarge      12h
cx1.2xlarge1gi   12h
cx1.4xlarge      12h
...
cx1.large        12h
cx1.medium       12h
cx1.xlarge       12h

NAME                       AGE
fedora                     12h
...
rhel.10                    12h
rhel.8                     12h
rhel.9                     12h
rhel.9.desktop             12h
...
ubuntu                     12h
windows.2k22               12h
windows.2k22.virtio        12h
```

| CONCEPT | ANSWERS | EXAMPLE |
|---|---|---|
| **Instance type** | *How big?* vCPU and memory | `u1.medium` = 1 vCPU, 4 GiB; `cx1.*` = compute-exclusive (dedicated CPUs) |
| **Preference** | *What OS?* best devices for it | `rhel.9`: virtio disks and NICs; `windows.2k22`: Hyper-V enlightenments, TPM |
| **DataSource** | *Which disk image?* | `rhel9` |

Instance type + preference + DataSource = a new VM in three clicks. They replace the older OpenShift **Templates** (still present: `oc get templates -n openshift | grep rhel9`).

## Creating, starting and stopping a VM

> **SCREENSHOT S-05:** *Virtualization > Catalog > InstanceTypes*: the RHEL 9 boot source tile selected, series U, size *medium*, project `default`.

```yaml
# Reference: the same VM from YAML (oc apply -f rhel9-demo.yaml)
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata: {name: rhel9-demo, namespace: default}
spec:
  runStrategy: Always                        # Always | Halted | Manual | RerunOnFailure
  instancetype: {name: u1.medium}
  preference:   {name: rhel.9}
  dataVolumeTemplates:
  - metadata: {name: rhel9-demo-root}
    spec:
      sourceRef: {kind: DataSource, name: rhel9, namespace: openshift-virtualization-os-images}
      storage: {resources: {requests: {storage: 30Gi}}}
  template:
    spec:
      domain: {devices: {}}
      volumes:
      - {name: rootdisk, dataVolume: {name: rhel9-demo-root}}
      - name: cloudinitdisk
        cloudInitNoCloud: {userData: "#cloud-config\nuser: cloud-user\npassword: <PASSWORD>\nchpasswd: {expire: false}"}
```

| NEED | WEB CONSOLE | CLI |
|---|---|---|
| Start | VM > Actions > Start | `virtctl start rhel9-demo` or `oc patch vm rhel9-demo --type merge -p '{"spec":{"runStrategy":"Always"}}'` |
| Stop | Actions > Stop | `virtctl stop rhel9-demo` or `runStrategy: Halted` |
| Console | *Console* tab (VNC or serial) | `virtctl console rhel9-demo` / `virtctl vnc rhel9-demo` |
| Live migrate | Actions > Migrate | `virtctl migrate rhel9-demo` |
| Where is it running? | *Overview*, Node field | `oc get vmi rhel9-demo -o wide` |
| Snapshot | *Snapshots* tab | `oc create -f vmsnapshot.yaml` (`VirtualMachineSnapshot`) |

**admin@bastion — real capture**

```console
[admin@bastion ~]$ which virtctl || ls ~/bin
which: no virtctl in (/home/admin/bin:/home/admin/.local/bin:/home/admin/bin:/usr/local/bin:/usr/bin:/usr/local/sbin:/usr/sbin)
kubectl
oc
openshift-install
```

> **GOTCHA — REAL ISSUE** `virtctl` is not installed on the bastion yet, so every VM action in this lab was done in the web console or with `oc patch ... runStrategy`. Download it from the cluster itself: web console *?* menu > *Command Line Tools* > *virtctl* (Linux x86_64) (the Pod `hyperconverged-cluster-cli-download-...` in `openshift-cnv` serves that download). Put it in `~/bin`. The exams expect you to use it.

## The API you are now using

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc api-resources --api-group=kubevirt.io
NAME                                SHORTNAMES             APIVERSION       NAMESPACED   KIND
kubevirts                           kv,kvs                 kubevirt.io/v1   true         KubeVirt
virtualmachineinstancemigrations    vmim,vmims             kubevirt.io/v1   true         VirtualMachineInstanceMigration
virtualmachineinstancepresets       vmipreset,vmipresets   kubevirt.io/v1   true         VirtualMachineInstancePreset
virtualmachineinstancereplicasets   vmirs,vmirss           kubevirt.io/v1   true         VirtualMachineInstanceReplicaSet
virtualmachineinstances             vmi,vmis               kubevirt.io/v1   true         VirtualMachineInstance
virtualmachines                     vm,vms                 kubevirt.io/v1   true         VirtualMachine
```

Learn the short names: `oc get vm`, `oc get vmi`, `oc get vmim`. Use `oc explain vm.spec.runStrategy` (or any field path) whenever you forget a field: it is the built-in reference, and it is allowed in the exam.

> **PRODUCTION** Turn on the HyperConverged features you need deliberately (`oc edit hco -n openshift-cnv`): live-migration limits (`liveMigrationConfig.parallelMigrationsPerCluster`), a dedicated migration network, CPU model for mixed hardware, and the `HostPathProvisioner` only if you really want node-local disks. Set **eviction strategy** `LiveMigrate` so node drains (upgrades) move VMs instead of stopping them.

> **EXAM TIP** EX316 (Red Hat Certified Specialist in OpenShift Virtualization) covers: installing the operator, creating VMs from instance types and templates, VM disks and DataVolumes, VM networks (NAD, bridge), live migration and node maintenance, snapshots, cloning, importing VMs, and RBAC for VMs. Every chapter from here maps to it.

> **LAB — DO IT ON THE BASTION** (1) Install `virtctl` as described. (2) Create `rhel9-demo` from the YAML above in a project `virt-lab`. (3) Watch `oc get dv,vm,vmi,pods -n virt-lab -w` and write down the order objects appear. (4) Live-migrate it with `virtctl migrate rhel9-demo -n virt-lab` and watch `oc get vmim -n virt-lab` and the node change in `oc get vmi -o wide`. (5) Delete the project and run `oc project default`.

## Check yourself

**Q1. `oc get vm` shows `ubuntu-noble-24.04-cloudimg` but `oc get vmi` shows nothing. Is the VM broken?**
No, it is powered off. *Logic:* the VM object is the definition (like the `.vmx`); a VMI and its `virt-launcher` Pod exist only while it runs.

**Q2. A node shows `<none>` in the KVM column. What will happen to a VM scheduled there, and what do you check in vSphere?**
It will not be scheduled there (`Insufficient devices.kubevirt.io/kvm`). *Logic:* `virt-handler` only advertises KVM when `/dev/kvm` exists; on vSphere tick *Expose hardware assisted virtualization* on the node VM (powered off).

**Q3. Why do `win2k22` and `rhel7` DataSources exist but have no DataVolume?**
No image is imported for them automatically (Windows because of Microsoft licensing; RHEL 7 is not in the automatic boot-source list on this release). *Logic:* the DataSource is a pointer; you upload your own image (for example with `virtctl image-upload`) and point it there.

**Q4. What is the difference between `runStrategy: Always` and `RerunOnFailure`?**
`Always` restarts the VM whenever it stops, even after a guest shutdown; `RerunOnFailure` restarts only after a failure, so a clean shutdown from inside the guest stays off. *Logic:* it is the VM equivalent of vSphere HA restart vs "leave powered off".

**Q5. What is the supported way to get a shell on an RHCOS node?**
`oc debug node/<name>` then `chroot /host`. *Logic:* RHCOS is managed by the Machine Config Operator; SSH edits are not tracked and are overwritten, so access goes through the API and is audited.
