# Chapter 8 · Migration Toolkit for Virtualization (MTV)

**MTV** (upstream project *Forklift*) is the OpenShift operator that moves VMs from vSphere, Red Hat Virtualization, OpenStack, Hyper-V, OVA files or another OpenShift cluster into OpenShift Virtualization. It reads the source VM, copies its disks into PVCs, converts the guest (removes VMware Tools, adds virtio drivers and the QEMU guest agent, fixes the bootloader) with **virt-v2v**, and creates a `VirtualMachine` with the same CPU, memory, MACs and disks.

MTV never changes or deletes the source VM in a cold migration. It only reads it. That makes the first migration of any VM safe to try.

## The objects, in the order you create them

| ORDER | MTV OBJECT | MEANING | IN THIS LAB |
|---|---|---|---|
| 1 | **Provider** (source) | A connection to vCenter: URL, credentials Secret, optional VDDK image | `vcenter-lab` |
| (auto) | **Provider** (destination) | This OpenShift cluster | `host` |
| 2 | **NetworkMap** | Source port group → Pod network or a NAD | `vcenter-lab-net`: `110` → `default/vlan-110` |
| 3 | **StorageMap** | Source datastore → StorageClass + access mode + volume mode | `vcenter-lab-storage`: `DATASTORE-01` → `nfs-csi` RWX Filesystem |
| 4 | **Plan** | Which VMs, which maps, target project, cold or warm | `test-ubuntu` |
| 5 | **Migration** | One run of a plan (the *Start* button) | `test-ubuntu-1` |

## The operator

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get csv -n openshift-mtv
NAME                   DISPLAY                                         VERSION   RELEASE   REPLACES               PHASE
mtv-operator.v2.12.9   Migration Toolkit for Virtualization Operator   2.12.9              mtv-operator.v2.12.8   Succeeded
```

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get forkliftcontroller -n openshift-mtv; oc get pods -n openshift-mtv
NAME                  AGE
forklift-controller   12h
NAME                                                    READY   STATUS    RESTARTS   AGE
forklift-api-97fbd7cf-bzwh2                             1/1     Running   0          12h
forklift-cli-download-77d8546b5f-d9sxh                  1/1     Running   0          12h
forklift-controller-5f6b76dd7c-d9l8z                    2/2     Running   0          8h
forklift-operator-845fbf655b-47547                      1/1     Running   0          8h
forklift-ova-proxy-7f6dd84f44-pdtln                     1/1     Running   0          8h
forklift-ui-plugin-7d8fb5df84-kt6xj                     1/1     Running   0          8h
forklift-validation-7bf5749c7b-lng6x                    1/1     Running   0          12h
forklift-volume-populator-controller-685dc5768f-2mz58   1/1     Running   0          8h
```

| POD | JOB |
|---|---|
| `forklift-controller` (2 containers) | The brain: runs plans; its `inventory` container caches the vCenter inventory |
| `forklift-validation` | Checks each VM against rules (unsupported disks, snapshots, missing CBT...) and shows warnings on the plan |
| `forklift-api` | Webhooks that reject invalid objects |
| `forklift-ui-plugin` | The *Migration for Virtualization* pages in the web console |
| `forklift-volume-populator-controller` | Fills PVCs for copy-offload and other populators |
| `forklift-cli-download` | Serves the `kubectl-mtv` CLI plug-in |

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc api-resources --api-group=forklift.konveyor.io
NAME                           SHORTNAMES   APIVERSION                     NAMESPACED   KIND
conversions                                 forklift.konveyor.io/v1beta1   true         Conversion
forkliftcontrollers                         forklift.konveyor.io/v1beta1   true         ForkliftController
hooks                                       forklift.konveyor.io/v1beta1   true         Hook
hosts                                       forklift.konveyor.io/v1beta1   true         Host
hypervproviderservers                       forklift.konveyor.io/v1beta1   true         HyperVProviderServer
migrations                                  forklift.konveyor.io/v1beta1   true         Migration
networkmaps                                 forklift.konveyor.io/v1beta1   true         NetworkMap
openstackvolumepopulators      osvp,osvps   forklift.konveyor.io/v1beta1   true         OpenstackVolumePopulator
ovaproviderservers                          forklift.konveyor.io/v1beta1   true         OVAProviderServer
ovirtvolumepopulators          ovvp,ovvps   forklift.konveyor.io/v1beta1   true         OvirtVolumePopulator
plans                                       forklift.konveyor.io/v1beta1   true         Plan
providers                                   forklift.konveyor.io/v1beta1   true         Provider
storagemaps                                 forklift.konveyor.io/v1beta1   true         StorageMap
vspherexcopyvolumepopulators   vxvp,vxvps   forklift.konveyor.io/v1beta1   true         VSphereXcopyVolumePopulator
```

Two newer kinds worth knowing: **Hook** runs an Ansible playbook or script before or after a VM migrates (for example, stop an application cleanly), and **VSphereXcopyVolumePopulator** is *storage copy offload*: when the source datastore and the target StorageClass are on the same array, the array copies the disk itself (XCOPY) instead of sending it over the network.

## Step 1 · A vCenter account for MTV

MTV logs in to vCenter, reads the inventory, reads disks, takes snapshots (warm migration) and powers the source VM off at cutover. Give it its own account with exactly those privileges.

```powershell
# Reference: jump host, PowerCLI. Creates role MTV-Migration (18 privileges), SSO user mtv@vsphere.local,
# and a propagated permission on the vCenter root. Prompts for passwords, writes nothing to disk.
PS C:\> .\New-MtvVcenterAccount.ps1 -VCenter vcenter.example.com -SkipCertificateCheck
```

| PRIVILEGE GROUP | PRIVILEGES | WHY |
|---|---|---|
| Sessions | Validate session | Keep the API session alive |
| Datastore | Browse, Low level file operations | Find and read VMDKs |
| VM > Interaction | Power off, Power on | Shut down the source at cutover, power it on for rollback |
| VM > Guest operations | Query, Execute, Modify | Read the guest IP configuration (preserve static IPs) |
| VM > Provisioning | Allow disk access, read-only disk access, file access, VM download, file upload, Clone, Mark as template | Read disks through VDDK/NFC |
| VM > Snapshot management | Create, Remove | Consistent reads, CBT deltas |
| VM > Change configuration | Toggle disk change tracking | Turn on CBT for warm migration |

> **SCREENSHOT S-08:** vSphere Client, *Administration > Roles*: `MTV-Migration` with its privileges, and the *Permissions* tab of vCenter showing `mtv@vsphere.local` propagated.

## Step 2 · The source provider

```bash
# Reference: ~/ocp-install/add-mtv-vsphere-provider.sh (prompts for the mtv@vsphere.local password)
oc create secret generic vcenter-lab -n openshift-mtv \
  --from-literal=user=mtv@vsphere.local --from-literal=password="$PW" \
  --from-literal=url=https://vcenter.example.com/sdk --from-literal=insecureSkipVerify=true \
  --dry-run=client -o yaml \
  | oc label -f - --local -o yaml createdForResourceType=providers createdForProviderType=vsphere \
  | oc apply -f -
oc apply -f - <<'Y'
apiVersion: forklift.konveyor.io/v1beta1
kind: Provider
metadata: {name: vcenter-lab, namespace: openshift-mtv}
spec:
  type: vsphere
  url: https://vcenter.example.com/sdk
  secret: {name: vcenter-lab, namespace: openshift-mtv}
  settings: {sdkEndpoint: vcenter}
Y
```

> **GOTCHA — REAL ISSUE** The first attempt failed in MTV's admission webhook: the Secret carried `createdForResourceType=providers` but not `createdForProviderType=vsphere`, and MTV rejects one without the other. Both labels must arrive in the same request, which is why the script adds them with `oc label --local` before `oc apply`.

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get providers -n openshift-mtv
NAME          TYPE        STATUS   READY   CONNECTED   INVENTORY   URL                                        AGE
host          openshift   Ready    True    True        True                                                   12h
vcenter-lab   vsphere     Ready    True    True        True        https://vcenter.example.com/sdk   6h29m
```

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get provider vcenter-lab -n openshift-mtv -o yaml | sed -n "/^spec:/,\$p" | grep -vE "lastTransitionTime|observedGeneration" | head -60
spec:
  secret:
    name: vcenter-lab
    namespace: openshift-mtv
  settings:
    sdkEndpoint: vcenter
  type: vsphere
  url: https://vcenter.example.com/sdk
status:
  conditions:
  - category: Warn
    message: TLS is susceptible to machine-in-the-middle attacks when certificate
      verification is skipped.
    reason: SkipTLSVerification
    status: "True"
    type: ConnectionInsecure
  - category: Required
    message: Connection test, succeeded.
    reason: Tested
    status: "True"
    type: ConnectionTestSucceeded
  - category: Advisory
    message: Validation has been completed.
    reason: Completed
    status: "True"
    type: Validated
  - category: Required
    message: The inventory has been loaded.
    reason: Completed
    status: "True"
    type: InventoryCreated
  - category: Required
    message: The provider is ready.
    status: "True"
    type: Ready
  fingerprint: AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD
  phase: Ready
  secretResourceVersion: "386243"
```

How to read MTV conditions (the same pattern applies to maps and plans):

- **`category: Required`** conditions must all be `True` for the object to work: here *ConnectionTestSucceeded*, *InventoryCreated* and *Ready*.
- **`Warn`** and **`Advisory`** are information. *ConnectionInsecure* is the honest price of `insecureSkipVerify=true`.
- The `fingerprint` is the SHA-1 of vCenter's certificate. In production you paste the vCenter CA instead of skipping verification.
- There is **no `vddkInitImage`** under `settings`. This single missing line is why the test migration in Chapter 9 took 84 minutes for 10 GiB.

> **SCREENSHOT S-09:** *Migration for Virtualization > Providers*: `host` and `vcenter-lab` both Ready, with the vcenter-lab inventory counts (VMs, hosts, networks, datastores).

### VDDK: the line that makes it fast

**VDDK** is VMware's disk library, the same one backup products use. Red Hat may not redistribute it, so you download it from Broadcom and wrap it in a tiny image. With it, disk data comes straight from the ESXi host over NBD (port 902); without it, virt-v2v reads the disk over HTTPS through vCenter.

```bash
# Reference: build the VDDK image (bastion, podman), then point the provider at it
tar -xzf ~/VMware-vix-disklib-8.0.*.x86_64.tar.gz -C ~/vddk
cat > ~/vddk/Containerfile <<'EOF'
FROM registry.access.redhat.com/ubi9/ubi-minimal
USER 1001
COPY vmware-vix-disklib-distrib /vmware-vix-disklib-distrib
RUN mkdir -p /opt
ENTRYPOINT ["cp", "-r", "/vmware-vix-disklib-distrib", "/opt"]
EOF
podman build -t <REGISTRY>/openshift-mtv/vddk:8.0 ~/vddk && podman push <REGISTRY>/openshift-mtv/vddk:8.0
oc patch provider vcenter-lab -n openshift-mtv --type merge \
  -p '{"spec":{"settings":{"vddkInitImage":"<REGISTRY>/openshift-mtv/vddk:8.0"}}}'
```

## Step 3 · Network and storage maps

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get networkmap,storagemap -n openshift-mtv
NAME                                              READY   AGE
networkmap.forklift.konveyor.io/vcenter-lab-net   True    5h54m

NAME                                                  READY   AGE
storagemap.forklift.konveyor.io/vcenter-lab-storage   True    5h54m
```

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get networkmap vcenter-lab-net -n openshift-mtv -o yaml | sed -n "/^spec:/,\$p" | grep -vE "lastTransitionTime|observedGeneration"
spec:
  map:
  - destination:
      name: vlan-110
      namespace: default
      type: multus
    source:
      name: "VM-Network-110"
  provider:
    destination:
      name: host
      namespace: openshift-mtv
    source:
      name: vcenter-lab
      namespace: openshift-mtv
status:
  conditions:
  - category: Required
    message: The network map is ready.
    status: "True"
    type: Ready
  references:
  - id: network-1044
    name: "VM-Network-110"
```

`destination type: multus` = "a NAD" (Multus is the CNI that attaches secondary networks); the other choice, `type: pod`, would put the VM on the Pod network behind NAT. `references` shows MTV resolved the name `"110"` to vCenter's managed object ID `network-1044`. The quotes around `"110"` matter: unquoted, YAML would read it as a number.

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get storagemap vcenter-lab-storage -n openshift-mtv -o yaml | sed -n "/^spec:/,\$p" | grep -vE "lastTransitionTime|observedGeneration"
spec:
  map:
  - destination:
      accessMode: ReadWriteMany
      storageClass: nfs-csi
      volumeMode: Filesystem
    source:
      name: DATASTORE-01
  provider:
    destination:
      name: host
      namespace: openshift-mtv
    source:
      name: vcenter-lab
      namespace: openshift-mtv
status:
  conditions:
  - category: Required
    message: The storage map is ready.
    status: "True"
    type: Ready
  references:
  - id: datastore-1047
    name: DATASTORE-01
```

Every datastore that holds a disk of a VM in the plan must appear in the StorageMap, and every port group a NIC uses must appear in the NetworkMap, or the plan will not become Ready.

> **SCREENSHOT S-10:** *Migration for Virtualization > Network maps* and *Storage maps*: `vcenter-lab-net` (110 → default/vlan-110) and `vcenter-lab-storage` (DATASTORE-01 → nfs-csi), both Ready.

## Step 4 · What a Plan can do

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc explain plan.spec.type
GROUP:      forklift.konveyor.io
KIND:       Plan
VERSION:    v1beta1

FIELD: type <string>
ENUM:
    cold
    warm
    live
    conversion

DESCRIPTION:
    Migration type. e.g. "cold", "warm", "live", "conversion". Supersedes the
    `warm` boolean if set.
```

| TYPE | WHAT HAPPENS | DOWNTIME | NEEDS |
|---|---|---|---|
| `cold` | Source powered off (by MTV if running), disks copied, converted, VM created | The whole copy | Nothing extra |
| `warm` | Disks copied while the source runs, deltas copied repeatedly via CBT, short final cutover | Minutes (final delta + conversion) | CBT on the source, VDDK in practice |
| `live` | Live migration between two OpenShift clusters (KubeVirt to KubeVirt) | None | Source is OpenShift Virtualization, not vSphere |
| `conversion` | Run only the guest conversion on disks already in place (for example after storage copy offload) | Depends | Disks already on OpenShift |

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc explain plan.spec.targetPowerState
GROUP:      forklift.konveyor.io
KIND:       Plan
VERSION:    v1beta1

FIELD: targetPowerState <string>
ENUM:
    on
    off
    auto

DESCRIPTION:
    TargetPowerState specifies the desired power state of the target VM after
    migration.
    - "on": Target VM will be powered on after migration
    - "off": Target VM will be powered off after migration
    - "auto" or nil (default): Target VM will match the source VM's power state
```

> **TIP** `oc explain` works on every MTV and KubeVirt field and always matches the version installed on *your* cluster. When a blog post and `oc explain` disagree, believe `oc explain`.

> **PRODUCTION** One provider per vCenter, VDDK always, the vCenter CA instead of `insecureSkipVerify`, the MTV account scoped to the folders being migrated, one NetworkMap and StorageMap per source environment reused by every wave, and a dedicated **transfer network** (plan `transferNetwork`) so disk copies do not compete with VM traffic. For FC/iSCSI arrays with a supported vendor, use storage copy offload (`offloadPlugin.vsphereXcopyConfig` in the StorageMap).

> **EXAM TIP** For EX316 be able to create a provider, a NetworkMap, a StorageMap and a Plan both in the console and as YAML, start a Migration, and read why a Plan is not Ready from its conditions.

> **LAB — DO IT ON THE BASTION** Run `oc get provider vcenter-lab -n openshift-mtv -o jsonpath='{.status.conditions[?(@.type=="Ready")].message}'` and `oc get plan -n openshift-mtv -o custom-columns=NAME:.metadata.name,READY:.status.conditions[0].type`. Then open the provider in the console and count the VMs it sees: that number comes from the inventory container of `forklift-controller`, not from vCenter directly.

## Check yourself

**Q1. The provider shows `ConnectionInsecure=True`. Is the provider broken?**
No. It is a `Warn` category condition. *Logic:* only `Required` conditions decide readiness; this one records that TLS verification was skipped on purpose (lab vCenter has a self-signed certificate).

**Q2. A plan stays not Ready and says a datastore is not mapped. The VM has two disks on two datastores. What do you do?**
Add the second datastore to the StorageMap. *Logic:* MTV must know a target StorageClass for every disk; one missing mapping blocks the whole plan.

**Q3. Why does the NetworkMap use `type: multus` and not `type: pod`?**
The VM must keep an L2 presence on VLAN 110 like it had in VMware. *Logic:* `pod` gives a NATed IP on the cluster network (masquerade); `multus` attaches the VM to the bridge NAD with its own MAC on the real VLAN.

**Q4. Which single setting would have made the lab migration much faster, and why?**
`spec.settings.vddkInitImage` on the provider. *Logic:* without VDDK, virt-v2v reads the disk through vCenter over HTTPS (`nbdkit curl`), around 2 to 3 MB/s here; with VDDK it reads directly from ESXi over NBD.

**Q5. Can MTV damage the source VM in a cold migration?**
It powers it off if it is running, but it does not modify or delete it; rollback is "power the source back on". *Logic:* cold migration only reads the disks; nothing is written back to vSphere.
