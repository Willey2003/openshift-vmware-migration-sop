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
