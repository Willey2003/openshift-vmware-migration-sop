---
title: "The Lab Manual: OpenShift Virtualization and VMware Migration"
subtitle: "Field manual, migration edition. Linux, networking, OpenShift 4.22, OpenShift Virtualization and MTV, learned by running real commands on your own cluster."
author: "Gaganpreet Singh"
date: "5 October 2026"
docid: "FIELD MANUAL · MIGRATION EDITION · OCTOBER 2026"
pagetitle: "The Lab Manual: Migration Edition"
versions: "RHEL 10.0 bastion · OpenShift 4.22.15 · Kubernetes 1.35.6 · RHCOS 9.8 · OpenShift Virtualization 4.22.9 · MTV 2.12.9 · NMState 4.22 · csi-driver-nfs 4.11 · vSphere 8"
---

# How to use this book

> **NOTE** **Placeholders.** Every hostname, IP address, VLAN, datastore and port group in this public edition is a placeholder (`example.com`, `198.51.100.0/24`, `203.0.113.0/24`). The command output is real output from the author's lab build with those values substituted. Replace them with your own environment's values before running anything.

This is a hands-on field manual, written in the same style as *The Lab Manual (Complete Edition)*, but about one thing: moving virtual machines from VMware vSphere to Red Hat OpenShift Virtualization. It starts from a Linux box and an empty vSphere folder, and ends with a VMware VM booted as an OpenShift VM.

Every dark terminal window marked **real capture** was executed on the author's lab on 5 October 2026 and the output is pasted exactly as the system printed it (only very long outputs are trimmed, marked `...`). Every command in a real capture is read-only, so you can re-run any of them at any time without changing anything.

Blocks marked **Reference** are the commands that *changed* the lab: installing the cluster, creating the NFS export, installing the operators, creating the bridge, adding the vCenter provider, starting the migration. They are the exact scripts that were run (they live in `openshift-manual/install/` and `openshift-manual/migration/`), shown as reference because re-running them on a working cluster is either pointless or harmful.

Screenshots are marked with a dashed **Screenshot** box that names the exact screen and what to look for. Save your capture as `images/S-nn.png` next to the book and run `python3 build.py`: the picture replaces the box in the HTML, Word and PDF editions.

## The capture lab

| LAYER | WHAT RAN | WHY IT MATTERS TO YOU |
|---|---|---|
| Source | VMware vSphere 8, vCenter `vcenter`, ESXi `esxi01`..`esxi04` | The platform you are migrating from |
| Bastion | RHEL 10.0 VM `bastion.example.com` (198.51.100.24), 8 vCPU, 16 GiB, 1 TiB + 500 GiB | Your Linux workstation: `oc`, installer, NFS server |
| Cluster | OpenShift 4.22.15 compact cluster `ocp1`, 3 nodes (12 vCPU, 48 GiB each), agent-based install | Every node is control plane *and* worker |
| Storage | NFS export `/exports/ocp` on the bastion + `csi-driver-nfs` v4.11.0, StorageClass `nfs-csi` (RWX) | RWX disks make VM live migration possible |
| Virtualization | OpenShift Virtualization 4.22.9 (KubeVirt), nested virtualization on vSphere | Runs VMs as Pods |
| VM networking | Kubernetes NMState 4.22, linux bridge `br-vm` on NIC2, VLAN NADs `vlan-110`, `vlan-120`, `vlan-130` | The OpenShift version of a port group |
| Migration | Migration Toolkit for Virtualization (MTV) 2.12.9, provider `vcenter-lab` | Copies and converts VMware VMs |
| Test VM | `ubuntu-noble-24.04-cloudimg` (2 vCPU, 1 GiB, 10 GiB disk) | Migrated cold, end to end, in Chapter 9 |

> **NOTE** The cluster is a *proof of concept* on nested virtualization: OpenShift nodes are themselves VMware VMs. It behaves exactly like a production cluster for every command in this book, but it is slower (nested KVM) and its storage is one NFS export. Wherever production differs, a **PRODUCTION** box says how.

## Conventions

- `[admin@bastion ~]$` = run on the bastion as your normal user. Commands that need root are prefixed `sudo`.
- `PS C:\>` = run in PowerShell with VMware PowerCLI on the Windows jump host.
- **NOTE** boxes explain, **TIP** boxes save time, **EXAM TIP** boxes map content to the Red Hat exams (EX316 OpenShift Virtualization, EX280 OpenShift Administration, EX188/DO188, DO180).
- **GOTCHA — REAL ISSUE** boxes are problems actually hit while building this lab. Each one is a troubleshooting lesson.
- **LAB — DO IT ON THE BASTION** boxes are exercises. **Check yourself** sections end each chapter: questions, answers, and the logic behind each answer.
- `<ANGLE_BRACKETS>` = a value you replace with your own.

## The lab at a glance

```text
                     vSphere 8 (vCenter vcenter)
   ┌──────────────────────────────────────────────────────────────────────┐
   │ VLAN 110 198.51.100.0/27        PG-OCP-NODES 198.51.100.32/27            │
   │  ┌──────────────┐               ┌───────────┐┌───────────┐┌───────────┐│
   │  │ bastion .24  │  NFS, oc ───► │ocp-node-1  ││ocp-node-2  ││ocp-node-3  ││
   │  │ RHEL 10      │               │ .34       ││ .35       ││ .36       ││
   │  └──────────────┘               │NIC1 ens33 ││NIC1 ens33 ││NIC1 ens33 ││
   │                                 │NIC2 ens34 ││NIC2 ens34 ││NIC2 ens34 ││
   │  source VM: ubuntu-noble-...    └─────┬─────┘└─────┬─────┘└─────┬─────┘│
   │  (port group VM-Network-110)                    └─ trunk port group (VLAN 4095) ┘│
   └──────────────────────────────────────────────────────────────────────┘
   API VIP .37 (api.ocp1.example.com)   Ingress VIP .38 (*.apps)
```

## Rebuilding the lab from zero

```bash
# Reference: the order this lab was built in. Each step is a chapter.
# 1. Bastion: RHEL 10 VM, second 500 GB disk, NFS export           (Chapter 1, 5)
# 2. Network: three /27 VLANs, DNS records in AD, routes          (Chapter 2)
# 3. vSphere: 3 node VMs, agent ISO, boot                         (Chapter 3)
~/ocp-install/build-agent-iso.sh
openshift-install agent wait-for install-complete --dir ~/ocp1
# 4. Day 1: htpasswd admin, remove kubeadmin                     (Chapter 4)
~/ocp-install/add-htpasswd-admin.sh
# 5. Storage and operators                                         (Chapter 5, 6)
~/ocp-install/install-nfs-csi.sh
~/ocp-install/install-cnv-nmstate-mtv.sh
# 6. VM networks                                                   (Chapter 7)
~/ocp-install/vm-networks.sh
# 7. MTV provider, maps, plan, migration                           (Chapter 8, 9)
~/ocp-install/add-mtv-vsphere-provider.sh
oc apply -f ~/ocp-install/mtv-test/test-ubuntu-plan.yaml
oc apply -f ~/ocp-install/mtv-test/test-ubuntu-migration.yaml
```
