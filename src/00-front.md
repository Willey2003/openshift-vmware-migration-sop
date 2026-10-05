---
title: "SOP: Migrating Virtual Machines from VMware vSphere to Red Hat OpenShift Virtualization"
subtitle: "Beginner to advanced, web console (GUI) and command line (oc), lab-validated"
author: "Gaganpreet Singh"
date: "5 October 2026"
docid: "SOP-OCPV-MIG-001"
pagetitle: "VMware to OpenShift Virtualization SOP"
---

# Document control

| Field | Value |
|---|---|
| Document ID | SOP-OCPV-MIG-001 |
| Title | Migrating virtual machines from VMware vSphere to Red Hat OpenShift Virtualization |
| Version | 1.0 (draft for team review) |
| Owner | Gaganpreet Singh |
| Validated on | Lab cluster `ocp1`, 5 October 2026 |
| Product versions | OpenShift Container Platform 4.22.15 (Kubernetes 1.35.6), OpenShift Virtualization 4.22.9, Migration Toolkit for Virtualization (MTV) 2.12.9, Kubernetes NMState Operator 4.22, RHEL 10.0 bastion |
| Source platform | VMware vSphere 8 (vCenter `vcenter.example.com`, 4 ESXi hosts) |
| Review cycle | Every OpenShift minor release, or after any production wave |

## Revision history

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0 | 2026-10-05 | Gaganpreet Singh | First issue, written against the lab build |

## Approvals

| Role | Name | Signature | Date |
|---|---|---|---|
| Author | Gaganpreet Singh | | |
| Technical reviewer (Virtualization) | | | |
| Storage team | | | |
| Network team | | | |
| Change manager | | | |

# How to use this SOP

> **Placeholders.** Every hostname, IP address, VLAN, datastore and port group in this SOP is a placeholder taken from the documentation ranges (`example.com`, `192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`). The command output is real output from a lab build with those values substituted. Replace them with your own environment's values before running anything.

## Who it is for

This SOP is written for an infrastructure engineer who knows VMware vSphere well but is new to Linux, networking and Kubernetes. Every concept starts at the most basic level ("what is this and why does it exist"), then shows how to do it in the **web console (GUI)** and with the **command line (`oc`)**, then shows the **real output** captured from the lab, and finally explains how to read that output.

It is also written to be used as-is for a **production** migration. Where production differs from the lab (mostly storage: FC, iSCSI and NFS arrays instead of a single NFS export), the difference is called out in a box labelled **PRODUCTION**.

## Conventions

| You see | It means |
|---|---|
| `$ oc get nodes` | A command you type on the bastion (Linux jump host). Do not type the `$`. |
| Text in a grey block under a command | The real output from the lab on 5 October 2026. Your names, ages and IDs will differ. |
| `<ANGLE_BRACKETS>` | A value you must replace with your own (for example `<VCENTER_FQDN>`). |
| **GUI:** | Steps in the OpenShift web console or the vSphere Client. |
| **CLI:** | Steps with `oc` on the bastion. |
| **Why:** | The logic behind the step. Read it once; it is what makes the step stick. |
| **PRODUCTION:** | What changes in a production environment. |
| **SCREENSHOT S-nn** | Capture this screen and save it as `images/S-nn.png` next to this document. The build script places it automatically. |
| **Check yourself** | Review questions. Each answer explains its reasoning. |

## How each section is laid out

1. **Concept**: what it is, in plain language, with the VMware equivalent.
2. **GUI**: click path.
3. **CLI**: commands.
4. **Output**: real lab output and how to read it.
5. **Verify**: how you know it worked.
6. **Check yourself**: questions and answers with logic.

# Glossary: VMware terms mapped to OpenShift

If you remember one table from this document, make it this one.

| VMware vSphere | OpenShift / OpenShift Virtualization | Plain-language meaning |
|---|---|---|
| vCenter Server | OpenShift cluster API + web console | The place you manage everything from |
| ESXi host | Node (RHCOS server running KVM) | The physical or virtual server that runs VMs |
| Cluster (DRS/HA) | OpenShift cluster + scheduler | Decides which node runs a VM and restarts it if a node dies |
| VM | `VirtualMachine` (VM) object | The definition of the VM (CPU, RAM, disks, NICs) |
| Powered-on VM process | `VirtualMachineInstance` (VMI) + `virt-launcher` pod | The running copy of the VM |
| vMotion | Live migration | Move a running VM to another node with no downtime |
| Datastore | `StorageClass` | A type of storage you can create disks from |
| VMDK | `PersistentVolumeClaim` (PVC) / `DataVolume` | A VM disk |
| Thin/thick provisioning | StorageClass parameters, volume mode | How the disk is allocated |
| Port group (VLAN) | `NetworkAttachmentDefinition` (NAD) | A network a VM NIC can plug into |
| vSwitch / VDS uplink | Linux bridge or OVS bridge created by `NodeNetworkConfigurationPolicy` (NNCP) | The switch inside each host that VMs plug into |
| Resource pool / folder | Project (namespace) | A container for VMs with its own quotas and permissions |
| vCenter roles and permissions | RBAC (Roles, RoleBindings) | Who can do what |
| Templates / content library | Templates, `DataSource`, boot images | Golden images to create VMs from |
| VMware Tools | QEMU guest agent + VirtIO drivers | In-guest helpers for clean shutdown, IP reporting and fast drivers |
| Snapshot | `VirtualMachineSnapshot` | Point-in-time copy of a VM |
| VMware HCX / Converter | Migration Toolkit for Virtualization (MTV, upstream name Forklift) | The tool that moves VMs into OpenShift |
| Changed Block Tracking (CBT) | Used by MTV warm migration | Lets MTV copy only changed blocks |
| VDDK | VDDK init image used by MTV | VMware's fast disk-read library |

# Lab environment used in this SOP

| Item | Value |
|---|---|
| OpenShift cluster | `ocp1`, base domain `example.com` |
| Nodes | 3 combined control-plane and worker nodes `ocp-node-1/2/3`, 198.51.100.34, .35, .36 (subnet 198.51.100.32/27, gateway .33) |
| API VIP | `api.ocp1.example.com` = 198.51.100.37 |
| Ingress VIP | `*.apps.ocp1.example.com` = 198.51.100.38 |
| Web console | https://console-openshift-console.apps.ocp1.example.com |
| Node NICs | `ens33` = node IP (on OVS bridge `br-ex`), `ens34` = VM traffic (Linux bridge `br-vm`, VLAN trunk) |
| Bastion | `bastion.example.com`, 198.51.100.24, RHEL 10.0, user `admin`, `oc` in `~/bin` |
| Storage | NFS export `/exports/ocp` on the bastion (500 GB disk), CSI driver `nfs.csi.k8s.io`, StorageClass `nfs-csi` (default) |
| VM networks | NADs `vlan-110`, `vlan-120`, `vlan-130` in project `default` |
| vCenter | `vcenter.example.com` (203.0.113.9), MTV service account `mtv@vsphere.local` |
| DNS / NTP | AD DNS 203.0.113.251, .252; NTP 203.0.113.117, .118 |
| MTV objects | Provider `vcenter-lab`, NetworkMap `vcenter-lab-net`, StorageMap `vcenter-lab-storage`, Plan `test-ubuntu` |
| Test VM | `ubuntu-noble-24.04-cloudimg` (2 vCPU, 1 GB, 10 GB disk on datastore `DATASTORE-01`, port group `VM-Network-110`) |
