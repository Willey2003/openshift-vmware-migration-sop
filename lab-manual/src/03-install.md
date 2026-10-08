# Chapter 3 · Installing OpenShift with the Agent-Based Installer

OpenShift Container Platform (OCP) is Kubernetes plus everything a platform needs: an image registry, Routes, OAuth login, a web console, monitoring, and **Operators** that install and upgrade every component. The nodes run **Red Hat Enterprise Linux CoreOS (RHCOS)**, an immutable OS that the cluster manages itself. You do not SSH in and edit nodes; you change them through the API.

There are several ways to install. This lab uses the **agent-based installer**: you describe the cluster in two YAML files, the installer turns them into one bootable ISO, and you boot every node VM from it. No DHCP, no PXE, no load balancer appliance. It is the closest thing OpenShift has to "deploy an OVA".

## Sizing the lab

| ROLE | COUNT | vCPU | RAM | DISKS | NICs |
|---|---|---|---|---|---|
| Control plane + worker ("compact") | 3 | 12 | 48 GiB | 120 GB OS (`sda`) + 100 GB spare (`sdb`) | NIC1 PG-OCP-NODES, NIC2 VM trunk |
| Bastion (existing) | 1 | 8 | 16 GiB | 1 TB + 500 GB NFS | port group VM-Network-110 |

> **NOTE** A **compact** cluster has three nodes that are all control plane *and* worker. It is the smallest highly-available OpenShift, and the usual choice for a proof of concept. OpenShift Virtualization needs **hardware virtualization** inside the nodes, so on vSphere the node VMs must have *Expose hardware assisted virtualization to the guest OS* ticked (nested virtualization). Chapter 6 proves it worked.

## The two input files

```yaml
# Reference: install-config.yaml (the cluster)
apiVersion: v1
baseDomain: example.com
metadata:
  name: ocp1                 # becomes api.ocp1.example.com
compute:
  - name: worker
    replicas: 0                        # compact: no separate workers
controlPlane:
  name: master
  replicas: 3
networking:
  networkType: OVNKubernetes
  machineNetwork:
    - cidr: 198.51.100.32/27             # the node VLAN PG-OCP-NODES
  clusterNetwork:
    - cidr: 10.128.0.0/14              # Pod IPs (internal only)
      hostPrefix: 23
  serviceNetwork:
    - 172.30.0.0/16                    # Service IPs (internal only)
platform:
  baremetal:                           # "bring your own machines", VIPs handled by keepalived
    apiVIPs:     [198.51.100.37]
    ingressVIPs: [198.51.100.38]
pullSecret: 'PULL_SECRET'              # filled in by build-agent-iso.sh, never committed
sshKey: 'SSH_KEY'
```

```yaml
# Reference: agent-config.yaml (the machines), first host shown
apiVersion: v1beta1
kind: AgentConfig
metadata:
  name: ocp1
rendezvousIP: 198.51.100.34              # this node runs the installer service for the others
additionalNTPSources: [203.0.113.117, 203.0.113.118, 203.0.113.251, 203.0.113.252]
hosts:
  - hostname: ocp-node-1
    role: master
    rootDeviceHints: {deviceName: /dev/sda}
    interfaces:
      - {name: ens192, macAddress: 00:50:56:aa:00:11}   # NIC1, matched by MAC
      - {name: ens224, macAddress: 00:50:56:aa:00:12}   # NIC2, left without IP
    networkConfig:                     # nmstate syntax: static IP, DNS, default route
      interfaces:
        - name: ens192
          type: ethernet
          state: up
          mac-address: 00:50:56:aa:00:11
          ipv4: {enabled: true, dhcp: false, address: [{ip: 198.51.100.34, prefix-length: 27}]}
      dns-resolver: {config: {server: [203.0.113.251, 203.0.113.252]}}
      routes:
        config: [{destination: 0.0.0.0/0, next-hop-address: 198.51.100.33, next-hop-interface: ens192}]
  # ocp-node-2 (.35) and ocp-node-3 (.36) follow the same pattern with their own MACs
```

| FIELD | MEANING | VSPHERE ANALOGY |
|---|---|---|
| `machineNetwork` | Where the node IPs live | The port group's subnet |
| `clusterNetwork` / `serviceNetwork` | Private overlay ranges inside the cluster | NSX segments that never leave the hosts |
| `apiVIPs` / `ingressVIPs` | Floating IPs moved between nodes by keepalived | A VIP on a load balancer appliance |
| `rendezvousIP` | The node that coordinates the install | The VCSA deployment stage 1 |
| `macAddress` | How the ISO knows which config belongs to which VM | The MAC in the VM's NIC settings |

> **GOTCHA — REAL ISSUE** The agent config names the NICs `ens192`/`ens224`, but on these VMs RHCOS calls them `ens33`/`ens34` (you will see that in Chapter 7). The install still worked because every interface is matched by **MAC address**, not by name. Lesson: always copy MACs from vCenter into the agent config; names are a hint, MACs are the key.

## Build the ISO and boot

```bash
# Reference: on the bastion (needs ~/pull-secret.json from console.redhat.com, never stored in Git)
[admin@bastion ~]$ ~/ocp-install/build-agent-iso.sh
# -> ~/ocp1/agent.x86_64.iso
```

```powershell
# Reference: on the jump host: upload the ISO, attach to the three VMs, power on
PS C:\> .\Mount-OcpAgentIso.ps1 -VCenter vcenter.example.com -Replace   # copies the ISO from the bastion, uploads, attaches, powers on
```

```bash
# Reference: watch from the bastion
[admin@bastion ~]$ openshift-install agent wait-for bootstrap-complete --dir ~/ocp1
[admin@bastion ~]$ openshift-install agent wait-for install-complete  --dir ~/ocp1
```

**admin@bastion — real capture**

```console
[admin@bastion ~]$ tail -4 ~/ocp-install/download.log
openshift-install 4.22.15
built from commit cdaf698d75d93e6ed9bf6203c988c5321a086758
release image quay.io/openshift-release-dev/ocp-release@sha256:fed788eac1c99388dd9b78dda4d6a73e39b70abb00ca918d2bb456a97187f0c1
release architecture amd64
```

The installer binary *is* the version: `openshift-install 4.22.15` can only install 4.22.15, from the release image digest shown.

> **SCREENSHOT S-01:** vSphere Client, folder *OCP-NODES*: the three VMs `OCP-NODE-1/2/3`, each with the agent ISO in CD/DVD drive 1 and *Expose hardware assisted virtualization* ticked under CPU.

> **SCREENSHOT S-02:** Console of `OCP-NODE-1` during install: the agent TUI showing the rendezvous host and the three hosts discovered.

> **GOTCHA — REAL ISSUE** After the install, the ISO was detached and NIC2 moved while the VMs were running. `ocp-node-1` was stunned for about eight minutes (its journal is silent from 12:46 to 12:54 UTC, with no reboot, then a burst of catch-up messages). A stunned control-plane node can cost etcd its quorum. Detach ISOs and change NICs with the node **powered off**, one node at a time.

## First look at the cluster

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc version
Client Version: 4.22.15
Kustomize Version: v5.7.1
Server Version: 4.22.15
Kubernetes Version: v1.35.6
```

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get nodes -o wide
NAME        STATUS   ROLES                         AGE   VERSION   INTERNAL-IP   EXTERNAL-IP   OS-IMAGE                                                KERNEL-VERSION                 CONTAINER-RUNTIME
ocp-node-1   Ready    control-plane,master,worker   13h   v1.35.6   198.51.100.34   <none>        Red Hat Enterprise Linux CoreOS 9.8.20260915-0 (Plow)   5.14.0-687.48.1.el9_8.x86_64   cri-o://1.35.8-11.rhaos4.22.giteaf45bc.el9
ocp-node-2   Ready    control-plane,master,worker   13h   v1.35.6   198.51.100.35   <none>        Red Hat Enterprise Linux CoreOS 9.8.20260915-0 (Plow)   5.14.0-687.48.1.el9_8.x86_64   cri-o://1.35.8-11.rhaos4.22.giteaf45bc.el9
ocp-node-3   Ready    control-plane,master,worker   13h   v1.35.6   198.51.100.36   <none>        Red Hat Enterprise Linux CoreOS 9.8.20260915-0 (Plow)   5.14.0-687.48.1.el9_8.x86_64   cri-o://1.35.8-11.rhaos4.22.giteaf45bc.el9
```

Every node has all three roles (compact). The OS is RHCOS 9.8, the runtime is CRI-O (the container engine; there is no Docker on OpenShift).

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get clusterversion
NAME      VERSION   AVAILABLE   PROGRESSING   SINCE   STATUS
version   4.22.15   True        False         13h     Cluster version is 4.22.15
```

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get co
NAME                                       VERSION   AVAILABLE   PROGRESSING   DEGRADED   SINCE   MESSAGE
authentication                             4.22.15   True        False         False      8h      
baremetal                                  4.22.15   True        False         False      13h     
cloud-controller-manager                   4.22.15   True        False         False      13h     
...
console                                    4.22.15   True        False         False      13h     
...
etcd                                       4.22.15   True        False         False      13h     
image-registry                             4.22.15   True        False         False      13h     
ingress                                    4.22.15   True        False         False      13h     
...
kube-apiserver                             4.22.15   True        False         False      13h     
...
machine-config                             4.22.15   True        False         False      13h     
...
network                                    4.22.15   True        False         False      13h     
...
openshift-apiserver                        4.22.15   True        False         False      8h      
...
storage                                    4.22.15   True        False         False      13h     
```

All 34 **ClusterOperators** are `AVAILABLE=True`, `PROGRESSING=False`, `DEGRADED=False`: the cluster is healthy. A ClusterOperator is one platform component (DNS, ingress, etcd...) and its own operator reports its health here. This is the first command to run on any OpenShift cluster, the way you open *Monitor > Issues and Alarms* in vCenter. `authentication` and `openshift-apiserver` show `8h` instead of `13h` because they rolled out again when the htpasswd login was added (Chapter 4).

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get mcp
NAME     CONFIG                                             UPDATED   UPDATING   DEGRADED   MACHINECOUNT   READYMACHINECOUNT   UPDATEDMACHINECOUNT   DEGRADEDMACHINECOUNT   AGE
master   rendered-master-037aa616469dfd56bcb6da3a1c53e738   True      False      False      3              3                   3                     0                      13h
worker   rendered-worker-86954aea22b2b3fd68fa55f7dfd3eef1   True      False      False      0              0                   0                     0                      13h
```

A **MachineConfigPool** groups nodes that get the same OS configuration. On a compact cluster all three nodes are in `master` and the `worker` pool is empty (`MACHINECOUNT 0`). That is normal, and it matters later: an OS change (a MachineConfig) aimed at the `worker` pool would do nothing here; aim it at `master`.

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get infrastructure cluster -o jsonpath="{.status.platform}{\"\n\"}{.status.apiServerURL}{\"\n\"}"; oc get network.config cluster -o jsonpath="{.status.networkType}{\"\n\"}{.status.clusterNetwork}{\"\n\"}{.status.serviceNetwork}{\"\n\"}"
BareMetal
https://api.ocp1.example.com:6443
OVNKubernetes
[{"cidr":"10.128.0.0/14","hostPrefix":23}]
["172.30.0.0/16"]
```

The cluster reads back exactly what `install-config.yaml` asked for. `BareMetal` is the platform type even though the nodes are VMware VMs: OpenShift does not talk to vCenter at all in this design, so it cannot create or delete node VMs. That is a choice. A `vsphere` platform install would let OpenShift create node VMs and VMDK-backed volumes itself.

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc adm top nodes
NAME        CPU(cores)   CPU(%)   MEMORY(bytes)   MEMORY(%)   
ocp-node-1   942m         8%       9213Mi          19%         
ocp-node-2   1469m        12%      13384Mi         28%         
ocp-node-3   1157m        10%      11042Mi         23%         
```

`942m` = 942 **millicores**, a little under one CPU. An idle compact cluster with Virtualization and MTV installed uses about 1 core and 9 to 13 GiB per node: budget for that before sizing VMs.

> **PRODUCTION** Use separate infrastructure for control plane and VM workers (3 control plane + N bare-metal workers), size workers like ESXi hosts (the VMs' total vCPU and RAM plus 10 to 15 % for OpenShift), and install on bare metal, not nested. Nested virtualization is for labs only.

> **EXAM TIP** EX280 starts every task from a running cluster. Know `oc get clusterversion`, `oc get co`, `oc describe co <name>`, `oc get nodes`, `oc adm top nodes`, `oc get mcp` and `oc adm must-gather` by heart.

> **LAB — DO IT ON THE BASTION** (1) Run `oc describe co etcd` and find the line that lists the etcd members. (2) Run `oc get pods -n openshift-etcd` and count the etcd Pods: why three? (3) Run `oc get nodes --show-labels | tr , '\n' | grep node-role` and explain the three role labels.

## Check yourself

**Q1. The install-config says `compute replicas: 0`. Where do application Pods and VMs run?**
On the three control-plane nodes, which also carry the `worker` role. *Logic:* with zero workers the installer makes control-plane nodes schedulable (compact mode), shown in `ROLES control-plane,master,worker`.

**Q2. You build a new ISO after changing an IP in agent-config.yaml, but the node still comes up with the old IP. What did you most likely forget?**
To boot from the **new** ISO (the old one was still in the datastore or still attached). *Logic:* the ISO carries the configuration; nothing reads the YAML after it is built.

**Q3. One ClusterOperator shows `DEGRADED=True`. What do you run next?**
`oc describe co <name>` and read the `Conditions` messages, then look at the Pods in its namespace (`oc get pods -n openshift-<name>`). *Logic:* the operator reports the symptom; its Pods and events show the cause.

**Q4. Why does `oc get mcp` show the `worker` pool with 0 machines, and when does that bite you?**
Compact cluster: all nodes are in `master`. *Logic:* MachineConfigs target pools by label; one labelled for `worker` will never roll out here, so you must label it `master` (or create a custom pool).

**Q5. Why is the platform `BareMetal` when the nodes are vSphere VMs?**
The cluster was installed with the agent installer and `platform: baremetal` (no vCenter credentials). *Logic:* platform type decides what OpenShift integrates with; with `baremetal` it does not call vCenter, so storage and node VMs are managed outside the cluster.
