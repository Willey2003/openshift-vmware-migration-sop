---
title: "Quick Start: Connect to OpenShift and Migrate a VM from vCenter"
subtitle: "Web console and oc side by side, in one sitting"
author: "Gaganpreet Singh"
date: "5 October 2026"
docid: "QS-OCPV-MIG-001"
pagetitle: "OpenShift VM Migration Quick Start"
---

# About this guide

This is the short version. In one sitting it takes you from "I have an OpenShift cluster" to "my vCenter VM is running on OpenShift", with every step shown in the **web console (GUI)** and with **`oc` (CLI)**. For the reasons behind each step, production storage (FC, iSCSI, NFS), wave planning and troubleshooting, use the full SOP (`SOP.md`) in this repository.

**Validated on:** OpenShift 4.22.15, OpenShift Virtualization 4.22.9, Migration Toolkit for Virtualization (MTV) 2.12.9, vSphere 8.

> **Placeholders.** All names and addresses below are placeholders from the documentation ranges. Replace them with your own values.

## Your values

Fill this in before you start.

| Item | Placeholder used in this guide | Yours |
|---|---|---|
| Cluster API | `https://api.ocp1.example.com:6443` | |
| Web console | `https://console-openshift-console.apps.ocp1.example.com` | |
| OpenShift user | `ocpadmin` (cluster-admin) | |
| vCenter (VCSA) | `vcenter.example.com` | |
| vCenter service account | `mtv@vsphere.local` | |
| Source VM | `app-vm-01` | |
| VM port group | `VM-Network-110` (VLAN 110) | |
| VM datastore | `DATASTORE-01` | |
| OpenShift VM network (NAD) | `default/vlan-110` | |
| OpenShift StorageClass | `nfs-csi` | |
| Target project | `migrated-vms` | |

## Already in place (from the SOP, Part B)

- OpenShift Virtualization, the NMState operator and MTV are installed (`oc get csv -n openshift-cnv`, `-n openshift-mtv` show `Succeeded`).
- A StorageClass for VM disks exists, ideally ReadWriteMany.
- A bridge network for the VM VLAN exists (an NNCP plus a NAD such as `vlan-110`).
- The cluster nodes can reach vCenter on TCP 443 and every ESXi host on TCP 443 and 902.
- A vCenter account with the MTV role exists (SOP section 11).

# Part 1: Connect to OpenShift

## 1.1 Web console (GUI)

1. Open the console URL in a browser: `https://console-openshift-console.apps.ocp1.example.com`.
2. If you see a certificate warning in a lab, accept it. In production the ingress certificate should be trusted.
3. Choose your identity provider (for example `htpasswd` or your company's LDAP/SSO) and log in.
4. You land on **Home > Overview**. The left menu now includes **Virtualization** and **Migration for Virtualization** if both operators are installed.

> **SCREENSHOT Q-01:** The console login page showing the identity provider choice.
>
> **SCREENSHOT Q-02:** Home > Overview after login, with the Virtualization and Migration for Virtualization menus visible on the left.

**Find the cluster version:** **Administration > Cluster Settings**.

## 1.2 Get the `oc` command line tool

`oc` is a single file. Download it for the computer you will work from (a Linux bastion is best).

**From the console:** click the **?** icon (top right) > **Command Line Tools** > download **oc** for your OS. The same page offers **virtctl** (VM console and start/stop from the CLI). Download both.

> **SCREENSHOT Q-03:** The Command Line Tools page listing oc and virtctl downloads.

**On a Linux bastion**, install them into your home directory so no root access is needed:

```bash
mkdir -p ~/bin
tar -xzf ~/Downloads/oc.tar.gz -C ~/bin          # file name from the download page
tar -xzf ~/Downloads/virtctl.tar.gz -C ~/bin
echo 'export PATH=$HOME/bin:$PATH' >> ~/.bashrc && source ~/.bashrc
oc version --client
```

```text
Client Version: 4.22.15
Kustomize Version: v5.7.1
```

**On Windows**, unzip `oc.exe` and `virtctl.exe` into a folder such as `C:\tools`, add that folder to your PATH, and use PowerShell or Windows Terminal.

## 1.3 Log in with `oc`

**Option A, user name and password:**

```bash
oc login -u ocpadmin https://api.ocp1.example.com:6443
```

```text
The server uses a certificate signed by an unknown authority.
You can bypass the certificate check, but any data you send to the server could be intercepted by others.
Use insecure connections? (y/n): y

Authentication required for https://api.ocp1.example.com:6443 (openshift)
Username: ocpadmin
Password:
Login successful.
(output trimmed)
```

Answer `y` only in a lab. In production, pass the CA instead: `oc login --certificate-authority=ca.crt ...`.

**Option B, token from the console** (works for SSO users who have no password for `oc`):

1. In the console, click your user name (top right) > **Copy login command** > log in again > **Display Token**.
2. Copy the line `oc login --token=sha256~... --server=https://api.ocp1.example.com:6443` and paste it into your terminal.

> **SCREENSHOT Q-04:** The Display Token page with the token value blurred.

**Option C, kubeconfig file** (for automation or the installer's break-glass admin):

```bash
export KUBECONFIG=~/ocp1/auth/kubeconfig     # the file the installer created
oc whoami                                      # prints system:admin
```

Keep the installer kubeconfig locked away. It never expires and is full cluster admin.

## 1.4 Check you are connected

```bash
oc whoami
oc whoami --show-console
oc get nodes
oc get clusterversion
```

```text
ocpadmin
https://console-openshift-console.apps.ocp1.example.com

NAME         STATUS   ROLES                         AGE   VERSION
ocp-node-1   Ready    control-plane,master,worker   8h    v1.35.6
ocp-node-2   Ready    control-plane,master,worker   8h    v1.35.6
ocp-node-3   Ready    control-plane,master,worker   8h    v1.35.6

NAME      VERSION   AVAILABLE   PROGRESSING   SINCE   STATUS
version   4.22.15   True        False         7h43m   Cluster version is 4.22.15
```

**Useful extras:**

| Task | Command |
|---|---|
| Which cluster and user am I using? | `oc config current-context` |
| Switch between saved clusters | `oc config get-contexts`, then `oc config use-context <name>` |
| Choose a project | `oc project migrated-vms` |
| Log out (invalidates the token) | `oc logout` |

# Part 2: Migrate a VM from vCenter to OpenShift

## 2.1 What you will create

| Object | What it is | Name in this guide |
|---|---|---|
| Project | Where the migrated VM will live | `migrated-vms` |
| Provider (source) | MTV's connection to vCenter | `vcenter` |
| Provider (destination) | This cluster. It already exists | `host` |
| NetworkMap | vCenter port group to OpenShift network | `vcenter-net` |
| StorageMap | vCenter datastore to OpenShift StorageClass | `vcenter-storage` |
| Plan | Which VMs, which maps, cold or warm | `app-vm-01-plan` |
| Migration | One run of the plan (the Start button) | `app-vm-01-run1` |

All MTV objects live in the `openshift-mtv` project. Only the migrated VM goes into `migrated-vms`.

## 2.2 Prepare the source VM (vSphere Client)

1. No snapshots: **Snapshots > Manage Snapshots > Delete All**.
2. VMware Tools running (Summary tab).
3. Record its IP, gateway, DNS and MAC so you can compare afterwards.
4. Cold migration (this guide): shut the VM down cleanly. For warm migration, enable Changed Block Tracking instead (SOP section 13).

## 2.3 Step by step

### Step 1: Create the target project

| GUI | CLI |
|---|---|
| **Home > Projects > Create Project**, name `migrated-vms` | `oc new-project migrated-vms` |

### Step 2: Add vCenter as a source provider

**GUI:** **Migration for Virtualization > Providers > Create provider > VMware**, project `openshift-mtv`.

| Field | Value |
|---|---|
| Provider name | `vcenter` |
| Endpoint type | vCenter |
| URL | `https://vcenter.example.com/sdk` |
| VDDK init image | Your VDDK image (strongly recommended, see SOP section 12). Empty works, but slowly |
| Username / Password | `mtv@vsphere.local` / its password |
| Certificate | Lab: skip validation. Production: accept the shown fingerprint after comparing it with vCenter's |

Click **Create provider** and wait for **Ready**.

> **SCREENSHOT Q-05:** Create provider form for VMware, password blurred.
>
> **SCREENSHOT Q-06:** Providers list showing `vcenter` and `host` both Ready.

**CLI:**

```bash
read -rsp "Password for mtv@vsphere.local: " PW; echo
oc create secret generic vcenter -n openshift-mtv \
  --from-literal=user=mtv@vsphere.local --from-literal=password="$PW" \
  --from-literal=url=https://vcenter.example.com/sdk --from-literal=insecureSkipVerify=true \
  --dry-run=client -o yaml \
  | oc label -f - --local -o yaml createdForResourceType=providers createdForProviderType=vsphere \
  | oc apply -f -
unset PW

cat <<'EOF' | oc apply -f -
apiVersion: forklift.konveyor.io/v1beta1
kind: Provider
metadata: {name: vcenter, namespace: openshift-mtv}
spec:
  type: vsphere
  url: https://vcenter.example.com/sdk
  secret: {name: vcenter, namespace: openshift-mtv}
  settings:
    sdkEndpoint: vcenter
    # vddkInitImage: registry.example.com/mtv/vddk:8.0
EOF

oc wait provider/vcenter -n openshift-mtv --for=condition=Ready --timeout=300s
oc get providers -n openshift-mtv
```

```text
NAME      TYPE        STATUS   READY   CONNECTED   INVENTORY   URL                               AGE
host      openshift   Ready    True    True        True                                          7h8m
vcenter   vsphere     Ready    True    True        True        https://vcenter.example.com/sdk   2m
```

### Step 3: Map the network

**GUI:** **Migration for Virtualization > Network maps > Create network map**. Source provider `vcenter`, target `host`. Source network `VM-Network-110` to target `default/vlan-110`. Create.

> **SCREENSHOT Q-07:** Network map with VM-Network-110 mapped to default/vlan-110.

**CLI:**

```bash
cat <<'EOF' | oc apply -f -
apiVersion: forklift.konveyor.io/v1beta1
kind: NetworkMap
metadata: {name: vcenter-net, namespace: openshift-mtv}
spec:
  provider:
    source: {name: vcenter, namespace: openshift-mtv}
    destination: {name: host, namespace: openshift-mtv}
  map:
  - source: {name: VM-Network-110}
    destination: {type: multus, namespace: default, name: vlan-110}
EOF
```

Map every port group any NIC of the VM uses. Mapping to the **Pod network** instead gives the VM a new private IP, so use the bridge NAD to keep its address.

### Step 4: Map the storage

**GUI:** **Migration for Virtualization > Storage maps > Create storage map**. Source `DATASTORE-01` to target `nfs-csi`. Create.

> **SCREENSHOT Q-08:** Storage map with DATASTORE-01 mapped to nfs-csi.

**CLI:**

```bash
cat <<'EOF' | oc apply -f -
apiVersion: forklift.konveyor.io/v1beta1
kind: StorageMap
metadata: {name: vcenter-storage, namespace: openshift-mtv}
spec:
  provider:
    source: {name: vcenter, namespace: openshift-mtv}
    destination: {name: host, namespace: openshift-mtv}
  map:
  - source: {name: DATASTORE-01}
    destination: {storageClass: nfs-csi, accessMode: ReadWriteMany, volumeMode: Filesystem}
EOF
oc get networkmaps,storagemaps -n openshift-mtv
```

```text
NAME                                          READY   AGE
networkmap.forklift.konveyor.io/vcenter-net   True    1m

NAME                                              READY   AGE
storagemap.forklift.konveyor.io/vcenter-storage   True    1m
```

For FC or iSCSI StorageClasses use `volumeMode: Block`.

### Step 5: Create the plan

**GUI:** **Migration for Virtualization > Migration plans > Create plan**.

1. Source provider `vcenter`, search and tick `app-vm-01`.
2. Plan name `app-vm-01-plan`, target provider `host`, target project `migrated-vms`.
3. Network map `vcenter-net`, storage map `vcenter-storage`.
4. Migration type **Cold**.
5. **Create plan** and wait for **Ready**. Fix anything listed as Critical.

> **SCREENSHOT Q-09:** Create plan wizard with app-vm-01 selected.
>
> **SCREENSHOT Q-10:** Plan details page showing Ready.

**CLI:**

```bash
cat <<'EOF' | oc apply -f -
apiVersion: forklift.konveyor.io/v1beta1
kind: Plan
metadata: {name: app-vm-01-plan, namespace: openshift-mtv}
spec:
  type: cold                       # or warm
  targetNamespace: migrated-vms
  targetPowerState: "on"           # on, off, or auto (match the source)
  provider:
    source: {name: vcenter, namespace: openshift-mtv}
    destination: {name: host, namespace: openshift-mtv}
  map:
    network: {name: vcenter-net, namespace: openshift-mtv}
    storage: {name: vcenter-storage, namespace: openshift-mtv}
  vms:
  - name: app-vm-01
EOF
oc get plan app-vm-01-plan -n openshift-mtv
```

```text
NAME             READY   EXECUTING   SUCCEEDED   FAILED   AGE
app-vm-01-plan   True                                     30s
```

### Step 6: Start the migration

**GUI:** on the plan, click **Start** and confirm.

**CLI:** creating a Migration object is the Start button.

```bash
cat <<'EOF' | oc apply -f -
apiVersion: forklift.konveyor.io/v1beta1
kind: Migration
metadata: {name: app-vm-01-run1, namespace: openshift-mtv}
spec:
  plan: {name: app-vm-01-plan, namespace: openshift-mtv}
EOF
```

To try again after a failure, create a new Migration with a new name (`app-vm-01-run2`).

### Step 7: Watch it

**GUI:** open the plan > **Virtual machines** tab > expand `app-vm-01` to see each step with a progress bar.

> **SCREENSHOT Q-11:** Plan VM pipeline expanded, showing the steps and progress.

**CLI:**

```bash
oc get plan app-vm-01-plan -n openshift-mtv -o jsonpath='{range .status.migration.vms[*].pipeline[*]}{.name}{"\t"}{.phase}{"\t"}{.progress.completed}/{.progress.total}{"\n"}{end}'
```

```text
Initialize               Completed   0/1
DiskAllocation           Completed   10240/10240
ImageConversion          Completed   0/1
DiskTransferV2v          Running     3174/10240
VirtualMachineCreation   Pending     0/1
```

| Step | What it does |
|---|---|
| Initialize | Checks the plan and prepares the target |
| DiskAllocation | Creates the disk (PVC) on the StorageClass and copies the data |
| ImageConversion / DiskTransferV2v | `virt-v2v` converts the guest: removes VMware Tools, adds VirtIO drivers, fixes the boot loader |
| VirtualMachineCreation | Creates the VirtualMachine with the same CPU, memory, MAC and mapped networks |

Live log of the conversion: `oc get pods -n migrated-vms`, then `oc logs -f <pod> -n migrated-vms`.

### Step 8: Check the VM on OpenShift

**GUI:** **Virtualization > VirtualMachines**, project `migrated-vms`. MTV makes the name DNS-safe (lower case, dots become dashes).

1. **Overview**: status Running, the node, and the IP address reported by the guest agent.
2. **Console**: log in and check `ip a` (Linux) or `ipconfig` (Windows) against what you recorded.
3. **Actions > Migrate** once to prove it can live-migrate between nodes.

> **SCREENSHOT Q-12:** VirtualMachine Overview, Running, with IP and node.
>
> **SCREENSHOT Q-13:** VM Console tab after login showing the original IP.

**CLI:**

```bash
oc get vm,vmi -n migrated-vms -o wide
virtctl console app-vm-01 -n migrated-vms       # Ctrl+] to leave
virtctl migrate app-vm-01 -n migrated-vms        # live migration test
```

### Step 9: Finish or roll back

- **All good:** keep the source VM **powered off** (not deleted) until the application owner signs off, then delete it in vSphere and archive the plan.
- **Roll back:** stop the OpenShift VM (**Actions > Stop** or `virtctl stop app-vm-01 -n migrated-vms`), power the source VM on in vSphere, and check the application. MTV does not change the source VM's disks, so it is exactly as you left it.

## 2.4 Warm migration in two lines

For VMs that cannot be off for the whole copy: enable CBT on the VM, set `type: warm` on the plan, start it, and at your change window set the cutover:

```bash
oc patch migration app-vm-01-run1 -n openshift-mtv --type merge \
  -p "{\"spec\":{\"cutover\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}}"
```

In the GUI, use the **Cutover** button on the plan.

# Part 3: If something goes wrong

| Symptom | Check | Usual fix |
|---|---|---|
| `oc login` says x509 / unknown authority | The API certificate is not trusted | Use `--certificate-authority=<ca.crt>` (or answer `y` in a lab) |
| `oc login` cannot resolve `api...` | DNS on your workstation | Point it at the DNS that holds the cluster records |
| Provider not Ready | `oc get provider vcenter -n openshift-mtv -o yaml` conditions | Fix URL, password, DNS or port 443 to vCenter |
| Plan not Ready: network or storage not mapped | Plan concerns in the GUI | Add the missing port group or datastore to the map |
| Disk copy very slow | Conversion pod log shows `nbdkit curl` | Add a VDDK image to the provider |
| VM boots but has no network | NIC mapped to the Pod network, or wrong VLAN | Fix the NetworkMap or the NAD VLAN, migrate again |

# Checklist

| # | Done | Item |
|---|---|---|
| 1 | | Logged in to the console and saw the Virtualization and Migration menus |
| 2 | | `oc whoami` and `oc get nodes` work from the bastion |
| 3 | | Source VM checked: no snapshots, tools running, IP recorded |
| 4 | | Provider `vcenter` Ready |
| 5 | | NetworkMap and StorageMap Ready |
| 6 | | Plan Ready, migration started |
| 7 | | VM Running on OpenShift with its original IP |
| 8 | | Live migration test passed |
| 9 | | Application owner signed off; source VM kept powered off until hypercare ends |
