# Chapter 9 · End to End: Migrating `ubuntu-noble-24.04-cloudimg`

This chapter is the whole migration of one real VM, in the order it happened on 5 October 2026. Do it once in the web console and once with `oc`; they create the same objects.

| | SOURCE (VMWARE) | TARGET (OPENSHIFT) |
|---|---|---|
| VM | `ubuntu-noble-24.04-cloudimg`, MoRef `vm-2275`, powered off | `ubuntu-noble-24.04-cloudimg` in project `mtv-test` |
| CPU / RAM | 2 vCPU, 1 GiB | 2 sockets × 1 core, 1 GiB |
| Disk | 10 GiB on datastore `DATASTORE-01` | PVC on `nfs-csi`, RWX, Filesystem |
| Network | port group `VM-Network-110` | NAD `default/vlan-110` (bridge, VLAN 110) |
| Type | | Cold, no VDDK |

## 9.1 Create the target project

```bash
# Reference
[admin@bastion ~]$ oc new-project mtv-test
```

## 9.2 The plan, in the web console

1. Log in as `ocpadmin`. Open **Migration for Virtualization > Providers** and check `vcenter-lab` and `host` are both **Ready**.
2. **Migration plans > Create plan**. **Source provider:** `vcenter-lab`.
3. **Select VMs:** search `ubuntu`, tick `ubuntu-noble-24.04-cloudimg`. Look at the *Concerns* column: MTV's validation service flags problems here before you start.
4. **Plan name:** `test-ubuntu`. **Target provider:** `host`. **Target project:** `mtv-test`.
5. **Network map:** source `VM-Network-110` → target `default/vlan-110`. Do not choose *Pod network*.
6. **Storage map:** source `DATASTORE-01` → target `nfs-csi`.
7. **Migration type:** Cold. Create the plan and wait for **Ready**.

> **SCREENSHOT S-11:** *Create plan*, VM selection step: `ubuntu-noble-24.04-cloudimg` ticked, with its Concerns column.

> **SCREENSHOT S-12:** *Create plan*, maps step: 110 → default/vlan-110 and DATASTORE-01 → nfs-csi.

> **SCREENSHOT S-13:** Plan `test-ubuntu` details page, status *Ready*, with the *Start* button. A notice that VDDK is not configured is expected in this lab.

## 9.3 The same plan with `oc`

```yaml
# Reference: ~/ocp-install/mtv-test/test-ubuntu-plan.yaml (NetworkMap + StorageMap + Plan)
apiVersion: forklift.konveyor.io/v1beta1
kind: NetworkMap
metadata: {name: vcenter-lab-net, namespace: openshift-mtv}
spec:
  provider:
    source: {name: vcenter-lab, namespace: openshift-mtv}
    destination: {name: host, namespace: openshift-mtv}
  map:
  - source: {name: "VM-Network-110"}                                             # vSphere port group
    destination: {type: multus, namespace: default, name: vlan-110}   # bridge br-vm, VLAN 110
---
apiVersion: forklift.konveyor.io/v1beta1
kind: StorageMap
metadata: {name: vcenter-lab-storage, namespace: openshift-mtv}
spec:
  provider:
    source: {name: vcenter-lab, namespace: openshift-mtv}
    destination: {name: host, namespace: openshift-mtv}
  map:
  - source: {name: DATASTORE-01}                             # vSphere datastore
    destination: {storageClass: nfs-csi, accessMode: ReadWriteMany, volumeMode: Filesystem}
---
apiVersion: forklift.konveyor.io/v1beta1
kind: Plan
metadata: {name: test-ubuntu, namespace: openshift-mtv}
spec:
  warm: false                                                          # cold
  targetNamespace: mtv-test
  provider:
    source: {name: vcenter-lab, namespace: openshift-mtv}
    destination: {name: host, namespace: openshift-mtv}
  map:
    network: {name: vcenter-lab-net, namespace: openshift-mtv}
    storage: {name: vcenter-lab-storage, namespace: openshift-mtv}
  vms:
  - name: ubuntu-noble-24.04-cloudimg
```

```bash
# Reference
[admin@bastion ~]$ oc apply -f ~/ocp-install/mtv-test/test-ubuntu-plan.yaml
```

Twelve lines of Plan were applied, but the API filled in the rest with defaults. This is what the cluster actually stored:

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get plan test-ubuntu -n openshift-mtv -o yaml | sed -n "/^spec:/,/^status:/p"
spec:
  deleteVmOnFailMigration: true
  map:
    network:
      name: vcenter-lab-net
      namespace: openshift-mtv
    storage:
      name: vcenter-lab-storage
      namespace: openshift-mtv
  migrateSharedDisks: true
  preserveStaticIPs: true
  provider:
    destination:
      name: host
      namespace: openshift-mtv
    source:
      name: vcenter-lab
      namespace: openshift-mtv
  pvcNameTemplateUseGenerateName: true
  runPreflightInspection: true
  skipGuestConversion: false
  targetNamespace: mtv-test
  useCompatibilityMode: true
  vms:
  - name: ubuntu-noble-24.04-cloudimg
  warm: false
  xfsCompatibility: false
status:
```

| DEFAULT | MEANING | WHEN YOU CHANGE IT |
|---|---|---|
| `deleteVmOnFailMigration: true` | A failed run cleans up its half-built target VM | Set `false` to keep it for debugging |
| `preserveStaticIPs: true` | virt-v2v writes the source's static IP config onto the new NIC | Set `false` if the VM gets a new IP on OpenShift |
| `runPreflightInspection: true` | Inspect the guest before copying, fail fast on unsupported OS | Rarely |
| `skipGuestConversion: false` | Run virt-v2v (drivers, guest agent, bootloader) | `true` only for already-KVM-ready guests |
| `useCompatibilityMode: true` | Use SATA/E1000-style devices if virtio drivers might be missing | `false` once you trust the guest has virtio |
| `migrateSharedDisks: true` | Copy multi-writer disks with this plan | `false` when another plan owns the shared disk |

## 9.4 Start it: the Migration object

```yaml
# Reference: ~/ocp-install/mtv-test/test-ubuntu-migration.yaml
apiVersion: forklift.konveyor.io/v1beta1
kind: Migration
metadata: {name: test-ubuntu-1, namespace: openshift-mtv}
spec:
  plan: {name: test-ubuntu, namespace: openshift-mtv}
```

```bash
# Reference: equivalent to pressing Start
[admin@bastion ~]$ oc apply -f ~/ocp-install/mtv-test/test-ubuntu-migration.yaml
[admin@bastion ~]$ oc get migration test-ubuntu-1 -n openshift-mtv -w
```

> **NOTE** A Migration is one *run* of a plan. To run the same plan again (for example after fixing something), create a new Migration with a new name: `test-ubuntu-2`.

> **SCREENSHOT S-14:** Plan `test-ubuntu` while running: the VM row expanded, showing the pipeline steps with *ImageConversion* in progress.

## 9.5 Watching the pipeline

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get plan test-ubuntu -n openshift-mtv -o jsonpath="{range .status.migration.vms[*].pipeline[*]}{.name}{\"\t\"}{.phase}{\"\t\"}{.started}{\"\t\"}{.completed}{\"\t\"}{.progress.completed}/{.progress.total}{\"\n\"}{end}" | column -t
Initialize              Completed  2026-10-05T15:08:05Z  2026-10-05T15:08:15Z  0/1
DiskAllocation          Completed  2026-10-05T15:08:15Z  2026-10-05T15:08:21Z  10240/10240
ImageConversion         Completed  2026-10-05T15:08:21Z  2026-10-05T15:31:47Z  0/1
DiskTransferV2v         Completed  2026-10-05T15:31:47Z  2026-10-05T16:32:29Z  10240/10240
VirtualMachineCreation  Completed  2026-10-05T16:32:29Z  2026-10-05T16:32:30Z  0/1
```

| STEP | DURATION | WHAT HAPPENED |
|---|---|---|
| Initialize | 10 s | Plan validated, VM looked up in the inventory |
| DiskAllocation | 6 s | 10 240 MB PVC created on `nfs-csi` |
| ImageConversion | **23 min 26 s** | virt-v2v pod inspected and converted the guest, reading through vCenter |
| DiskTransferV2v | **60 min 42 s** | The disk data copied, about 2.8 MB/s (no VDDK) |
| VirtualMachineCreation | 1 s | `VirtualMachine` object created from the converted definition |
| **Total** | **1 h 24 min 25 s** | 15:08:05 to 16:32:30 UTC |

> **TIP** `progress 0/1` on Initialize, ImageConversion and VirtualMachineCreation is not a bug: those steps are counted as a single unit and only show complete/incomplete. Disk steps count megabytes.

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get plan test-ubuntu -n openshift-mtv -o jsonpath="{range .status.migration.vms[*]}{.name}{\" id=\"}{.id}{\" phase=\"}{.phase}{\" started=\"}{.started}{\" completed=\"}{.completed}{\"\n\"}{end}"
ubuntu-noble-24.04-cloudimg id=vm-2275 phase=Completed started=2026-10-05T15:08:05Z completed=2026-10-05T16:32:30Z
```

### The conversion Pod

MTV ran one Pod in the target project that did both conversion and copy:

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get pods -n mtv-test
NAME                        READY   STATUS      RESTARTS   AGE
test-ubuntu-vm-2275-m2czj   0/1     Completed   0          5h53m
```

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get pod test-ubuntu-vm-2275-m2czj -n mtv-test -o jsonpath="{.spec.containers[*].image}{\"\n\"}{.status.startTime}{\"\n\"}"; oc logs test-ubuntu-vm-2275-m2czj -n mtv-test --tail=25
registry.redhat.io/migration-toolkit-virtualization/mtv-virt-v2v-rhel10@sha256:36266543303e3b78841c7894f7fc745ab8351507b638bc9766d0b378733180b5
2026-10-05T15:08:25Z
fsync /dev/sda
guestfsd: => internal_autosync (0x11a) took 0.04 secs
libguestfs: calling virDomainDestroy flags=VIR_DOMAIN_DESTROY_GRACEFUL
libguestfs: closing guestfs handle 0x5580566ff990 (state 0)
libguestfs: command: run: rm
libguestfs: command: run: \ -rf /tmp/libguestfsc3zBcz
libguestfs: command: run: rm
libguestfs: command: run: \ -rf /tmp/libguestfs2vsD5h
Starting server on :8080
2026/10/05 16:32:29 http: superfluous response.WriteHeader call from github.com/kubev2v/forklift/pkg/virt-v2v/server.Server.vmHandler (server.go:81)
2026/10/05 16:32:29 http: superfluous response.WriteHeader call from github.com/kubev2v/forklift/pkg/virt-v2v/server.Server.inspectorHandler (server.go:99)
Shutdown request received. Shutting down server.
```

Read it bottom-up. The image is `mtv-virt-v2v-rhel10`: virt-v2v runs in a RHEL 10 container. *libguestfs* is the library virt-v2v uses to open the guest disk inside a tiny appliance VM; `fsync /dev/sda` and `virDomainDestroy` are it flushing and closing that appliance. Then the Pod serves the converted VM definition on `:8080`, the controller fetches it at 16:32:29 (the exact second *VirtualMachineCreation* started), and the Pod shuts down. The two `superfluous response.WriteHeader` lines are harmless Go HTTP warnings.

> **GOTCHA — REAL ISSUE** *No VDDK = slow.* During the copy the same Pod's log showed `nbdkit: curl[4]: ...` and `virt-v2v monitoring: Progress update, completed 17 %`: the disk was being read over HTTPS through vCenter with nbdkit's curl plug-in. 10 GiB took 61 minutes; a 500 GiB production disk would take more than two days. Configure VDDK (Chapter 8) before any real wave.

## 9.6 What MTV built

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get plan,migration -n openshift-mtv
NAME                                    READY   EXECUTING   SUCCEEDED   FAILED   AGE
plan.forklift.konveyor.io/test-ubuntu   True                True                 5h54m

NAME                                           READY   RUNNING   SUCCEEDED   FAILED   AGE
migration.forklift.konveyor.io/test-ubuntu-1   True              True                 5h53m
```

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get plan test-ubuntu -n openshift-mtv -o jsonpath="{range .status.conditions[*]}{.type}{\"\t\"}{.status}{\"\t\"}{.message}{\"\n\"}{end}"
Ready	True	The migration plan is ready.
Succeeded	True	The plan execution has SUCCEEDED.
```

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get vm,vmi -n mtv-test; oc get pvc,dv -n mtv-test
NAME                                                     AGE     STATUS    READY
virtualmachine.kubevirt.io/ubuntu-noble-24.04-cloudimg   4h29m   Stopped   False
NAME                                              STATUS   VOLUME                                     CAPACITY      ACCESS MODES   STORAGECLASS   VOLUMEATTRIBUTESCLASS   AGE
persistentvolumeclaim/test-ubuntu-vm-2275-dm9s9   Bound    pvc-e18f93ca-a91e-46e9-9306-2bda130b4efd   11381663335   RWX            nfs-csi        <unset>                 5h53m
```

The VM is `Stopped` because the source was powered off and `targetPowerState` defaults to `auto` (match the source). There is no VMI (not running) and no DataVolume: MTV fills PVCs directly with a volume populator. The PVC name `test-ubuntu-vm-2275-dm9s9` is *plan name - source MoRef - random suffix* (`pvcNameTemplateUseGenerateName: true`).

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get vm ubuntu-noble-24.04-cloudimg -n mtv-test -o yaml | sed -n "/^spec:/,/^status:/p" | head -80
spec:
  preference:
    kind: virtualmachineclusterpreference
    name: linux
  runStrategy: Halted
  template:
    metadata:
      annotations:
        kubevirt.io/pci-topology-version: v3
        vm.kubevirt.io/flavor: ""
        vm.kubevirt.io/os: ""
        vm.kubevirt.io/workload: ""
      labels:
        app: ubuntu-noble-24.04-cloudimg
    spec:
      architecture: amd64
      domain:
        clock:
          timezone: UTC
        cpu:
          cores: 1
          sockets: 2
        devices:
          disks:
          - bootOrder: 1
            disk:
              bus: virtio
            name: vol-0
            serial: 6000C290-0000-0000-0
          - disk:
              bus: virtio
            name: cloudinitdisk
          inputs:
          - bus: virtio
            name: tablet
            type: tablet
          interfaces:
          - bridge: {}
            macAddress: 00:50:56:aa:00:01
            model: virtio
            name: net-0
          tpm:
            enabled: false
        firmware:
          bootloader:
            bios: {}
          serial: VMware-42 00 00 00 00 00 00 00-00 00 00 00 00 00 00 01
          uuid: f9b9a01b-3c5d-4463-a614-92cb4ba5b3f5
        machine:
          type: pc-q35-rhel9.8.0
        memory:
          guest: 1Gi
        resources: {}
      networks:
      - multus:
          networkName: default/vlan-110
        name: net-0
      volumes:
      - name: vol-0
        persistentVolumeClaim:
          claimName: test-ubuntu-vm-2275-dm9s9
      - cloudInitNoCloud:
          secretRef:
            name: ubuntu-noble-24.04-cloudimg-cloudinit
        name: cloudinitdisk
status:
```

Every field traced back to its source:

| FIELD | VALUE | WHERE IT CAME FROM |
|---|---|---|
| `runStrategy` | `Halted` | Source was powered off, `targetPowerState: auto` |
| `cpu` | 2 sockets × 1 core | Source vCPU topology, copied exactly |
| `memory.guest` | `1Gi` | Source RAM |
| `firmware.bootloader` | `bios` | Source firmware (BIOS, not EFI) |
| `firmware.serial` | `VMware-42 00 00 ...` | The VMware BIOS serial, kept so licence checks keyed to it still pass |
| `disks[vol-0].serial` | `6000C290-...` | The VMDK's UUID, kept so `/dev/disk/by-id` paths in the guest do not change |
| `interfaces[net-0].macAddress` | `00:50:56:aa:00:01` | The source NIC's VMware MAC, kept so DHCP reservations and MAC-bound configs keep working |
| `interfaces[net-0].bridge`, `networks.multus` | `default/vlan-110` | The NetworkMap |
| `disk bus: virtio` | | virt-v2v installed virtio drivers, so it used the fast bus |
| `volumes[vol-0]` | PVC `test-ubuntu-vm-2275-dm9s9` | The StorageMap |
| `preference: linux` | | Chosen by MTV from the detected guest OS |
| `cloudinitdisk` volume | Secret `...-cloudinit` | **Not** from MTV: added afterwards to give the cloud image a login (next section) |

> **GOTCHA — REAL ISSUE** The test-ubuntu README predicted that MTV would rename the VM (dots to dashes). The capture shows it did not: `ubuntu-noble-24.04-cloudimg` is a valid Kubernetes name, because dots are allowed in object names. MTV only renames VMs whose names are not valid (uppercase letters, spaces, underscores, longer than 63 characters). Check `oc get vm -n <project>` rather than guessing.

## 9.7 First boot

The Ubuntu *cloud image* has no password at all (it expects cloud-init), so the VM was given a login before its first start:

```bash
# Reference: ~/ocp-install/mtv-test/add-cloudinit-login.sh (prompts; only a SHA-512 hash is stored)
HASH=$(openssl passwd -6 "$PW")
oc create secret generic ubuntu-noble-24.04-cloudimg-cloudinit -n mtv-test --from-literal=userdata="#cloud-config
users:
- name: ubuntu
  lock_passwd: false
  passwd: '$HASH'
  sudo: ALL=(ALL) NOPASSWD:ALL
ssh_pwauth: true"
oc patch vm ubuntu-noble-24.04-cloudimg -n mtv-test --type=json -p '[
 {"op":"add","path":"/spec/template/spec/domain/devices/disks/-","value":{"name":"cloudinitdisk","disk":{"bus":"virtio"}}},
 {"op":"add","path":"/spec/template/spec/volumes/-","value":{"name":"cloudinitdisk","cloudInitNoCloud":{"secretRef":{"name":"ubuntu-noble-24.04-cloudimg-cloudinit"}}}}]'
```

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get secret -n mtv-test | grep -vE "dockercfg|token"
NAME                                    TYPE     DATA   AGE
ubuntu-noble-24.04-cloudimg-cloudinit   Opaque   1      41m
```

Then the VM was started from the console (*Virtualization > VirtualMachines > mtv-test > ubuntu-noble-24.04-cloudimg > Start*), checked in the *Console* tab, and shut down again. The project's events are the proof that it really booted on OpenShift:

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get events -n mtv-test --sort-by=.lastTimestamp | head -25
LAST SEEN   TYPE     REASON             OBJECT                                                MESSAGE
37m         Normal   Scheduled          pod/virt-launcher-ubuntu-noble-24.04-cloudimg-cstks   Successfully assigned mtv-test/virt-launcher-ubuntu-noble-24.04-cloudimg-cstks to ocp-node-1
37m         Normal   SuccessfulCreate   virtualmachineinstance/ubuntu-noble-24.04-cloudimg    Created virtual machine pod virt-launcher-ubuntu-noble-24.04-cloudimg-cstks
37m         Normal   SuccessfulCreate   virtualmachine/ubuntu-noble-24.04-cloudimg            Started the virtual machine by creating the new virtual machine instance ubuntu-noble-24.04-cloudimg
37m         Normal   Started            pod/virt-launcher-ubuntu-noble-24.04-cloudimg-cstks   Container started
37m         Normal   Created            pod/virt-launcher-ubuntu-noble-24.04-cloudimg-cstks   Container created
37m         Normal   Pulled             pod/virt-launcher-ubuntu-noble-24.04-cloudimg-cstks   Container image "registry.redhat.io/container-native-virtualization/virt-launcher-rhel9@sha256:a6e0242c8771071b8aa5d36193e9513740fde96ef560c6a1ff81a5e4ed78bbec" already present on machine and can be accessed by the pod
37m         Normal   AddedInterface     pod/virt-launcher-ubuntu-noble-24.04-cloudimg-cstks   Add pod900d2c01c91 [] from default/vlan-110
37m         Normal   AddedInterface     pod/virt-launcher-ubuntu-noble-24.04-cloudimg-cstks   Add eth0 [10.130.0.92/23] from ovn-kubernetes
...
37m         Normal   Started            virtualmachineinstance/ubuntu-noble-24.04-cloudimg    VirtualMachineInstance started.
37m         Normal   Created            virtualmachineinstance/ubuntu-noble-24.04-cloudimg    VirtualMachineInstance defined.
35m         Normal   ShuttingDown       virtualmachineinstance/ubuntu-noble-24.04-cloudimg    Signaled Graceful Shutdown
35m         Normal   SuccessfulDelete   virtualmachine/ubuntu-noble-24.04-cloudimg            Stopped the virtual machine by deleting the virtual machine instance 477a1f54-2b09-43f8-9d6a-9490393a2387
...
34m         Normal   Stopped            virtualmachineinstance/ubuntu-noble-24.04-cloudimg    The VirtualMachineInstance was shut down.
34m         Normal   SuccessfulDelete   virtualmachineinstance/ubuntu-noble-24.04-cloudimg    Deleted virtual machine pod virt-launcher-ubuntu-noble-24.04-cloudimg-cstks
```

Read it as a boot log of the *platform*:

1. *Started the virtual machine by creating the new virtual machine instance*: someone pressed Start; the VM controller created a VMI.
2. *Successfully assigned ... to ocp-node-1*: the scheduler placed the `virt-launcher` Pod on node 1 (it had `devices.kubevirt.io/kvm` and `br-vm`).
3. *Add eth0 [10.130.0.92/23] from ovn-kubernetes*: every Pod also gets a Pod-network interface; the VM does not use it here.
4. *Add pod900d2c01c91 [] from default/vlan-110*: **the VM's NIC was plugged into the VLAN 110 bridge.** The empty `[]` means OpenShift assigned no IP (`ipam: {}`): the guest gets its address itself, as it did on VMware.
5. *VirtualMachineInstance started*: QEMU/KVM is running the guest inside the Pod.
6. Two minutes later, *Signaled Graceful Shutdown* and *Stopped the virtual machine by deleting the virtual machine instance*: someone pressed Stop; the guest got an ACPI shutdown, then the Pod was removed.

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get vm ubuntu-noble-24.04-cloudimg -n mtv-test -o jsonpath="{.status.printableStatus}{\"\n\"}{range .status.conditions[*]}{.type}={.status} {.reason}{\"\n\"}{end}"
Stopped
Ready=False VMINotExists
LiveMigratable=True 
StorageLiveMigratable=True 
```

`Ready=False VMINotExists` is just "powered off". The two important lines are `LiveMigratable=True` and `StorageLiveMigratable=True`: thanks to the RWX StorageMap, this VM can be moved between nodes while running (vMotion) and between StorageClasses (Storage vMotion).

> **SCREENSHOT S-15:** *Virtualization > VirtualMachines > mtv-test > ubuntu-noble-24.04-cloudimg*, *Overview* tab while running: node `ocp-node-1`, network `vlan-110`, disk on `nfs-csi`.

> **SCREENSHOT S-16:** *Console* tab: Ubuntu 24.04 login prompt, then `ip a` showing the NIC with MAC `00:50:56:aa:00:01`.

## 9.8 Validate, roll back, clean up

| CHECK | HOW | EXPECTED |
|---|---|---|
| Boots | Console tab | Login prompt |
| Same MAC | `ip link` in the guest | `00:50:56:aa:00:01` |
| Network | `ip a`, `ping <gateway>` | Address on the VLAN 110 subnet, gateway answers (if the VLAN has DHCP or a static IP was preserved) |
| Guest agent | VM *Overview* shows OS name and IP | virt-v2v installed `qemu-guest-agent` |
| Disk | `lsblk` in the guest | 10 GiB `vda` |
| Application | Your smoke test | Same as on VMware |

**Rollback** for a cold migration is: stop the OpenShift VM, power the source VM back on in vSphere. Nothing on the VMware side was changed.

```bash
# Reference: clean up when done (keep the maps for the next VM)
oc delete project mtv-test
oc delete plan/test-ubuntu -n openshift-mtv
oc project default
```

> **EXAM TIP** EX316 can ask you to migrate a VM and then prove it: read the plan's pipeline, find the VM, start it, attach it to the right network, and show it is live-migratable. Everything in this chapter is the answer key.

> **LAB — DO IT ON THE BASTION** Migrate the same VM again as `test-ubuntu-2` into a project `mtv-test2`, but this time set `targetPowerState: "on"` and `preserveStaticIPs: false` in the plan. Compare the pipeline timings and the new VM's `runStrategy` with the first run.

## Check yourself

**Q1. The pipeline shows `DiskTransferV2v Running 1740/10240` for a long time. Is it stuck?**
Not necessarily. *Logic:* without VDDK the copy runs at a few MB/s; check that the number keeps rising (`-w`) and read the conversion Pod's log for `virt-v2v monitoring: Progress update`.

**Q2. Why does the migrated VM keep the VMware MAC `00:50:56:...`, and when would that be a problem?**
MTV copies the source MAC so DHCP reservations and MAC-bound guest configs keep working. *Logic:* it becomes a problem only if the source VM is powered on again on the same VLAN at the same time (duplicate MAC). Never run both.

**Q3. The VM is `Stopped` right after a successful migration. What decided that?**
`targetPowerState: auto` with a source that was powered off. *Logic:* auto = match the source; set `on` in the plan to start it automatically.

**Q4. In the events, the virt-launcher Pod got `eth0 10.130.0.92/23` from OVN-Kubernetes. Does the VM use that address?**
No. *Logic:* the VM's only interface `net-0` is a bridge on `default/vlan-110`; the Pod's `eth0` belongs to the launcher container itself.

**Q5. How do you run the same plan a second time?**
Create a new Migration object with a new name pointing at the plan. *Logic:* a Migration is a single, immutable run; its status is history.
