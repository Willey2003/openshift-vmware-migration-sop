# Appendix A · Command Cheat Sheets

## Cluster

| TASK | COMMAND |
|---|---|
| Who and where am I | `oc whoami; oc whoami --show-server; oc project` |
| Version and health | `oc get clusterversion; oc get co` |
| Unhealthy operators only | `oc get co \| awk 'NR==1 \|\| $3!="True" \|\| $5!="False"'` |
| Nodes, usage | `oc get nodes -o wide; oc adm top nodes` |
| Node shell | `oc debug node/<node> --to-namespace=default -- chroot /host` |
| Installed operators | `oc get csv -A; oc get subscription -A` |
| Field reference | `oc explain <kind>.<field>` |
| Events, newest last | `oc get events -n <ns> --sort-by=.lastTimestamp` |

## Storage

| TASK | COMMAND |
|---|---|
| Classes and default | `oc get sc` |
| Claims and volumes | `oc get pvc -A; oc get pv` |
| CDI's view of a class | `oc get storageprofile <sc> -o yaml` |
| DataVolumes and progress | `oc get dv -A` |
| Grow a disk | `oc patch pvc <pvc> -n <ns> -p '{"spec":{"resources":{"requests":{"storage":"20Gi"}}}}'` |

## Virtualization

| TASK | COMMAND |
|---|---|
| VMs and running instances | `oc get vm,vmi -A` |
| Start / stop | `virtctl start <vm>` / `virtctl stop <vm>` (or patch `runStrategy`) |
| Console | `virtctl console <vm>`, `virtctl vnc <vm>` |
| Live migrate | `virtctl migrate <vm>`; `oc get vmim -A` |
| Where is it | `oc get vmi <vm> -o wide` |
| KVM capacity per node | `oc get nodes -o custom-columns=NODE:.metadata.name,KVM:.status.allocatable.devices\.kubevirt\.io/kvm` |
| Boot sources | `oc get datasource -n openshift-virtualization-os-images` |
| Sizes and OS profiles | `oc get virtualmachineclusterinstancetype; oc get virtualmachineclusterpreference` |
| Platform health | `oc get hco -n openshift-cnv -o jsonpath='{range .status.conditions[*]}{.type}={.status}{"\n"}{end}'` |

## VM networking

| TASK | COMMAND |
|---|---|
| Policies and per-node result | `oc get nncp; oc get nnce` |
| A node's live interfaces | `oc get nns <node> -o yaml` |
| VM networks | `oc get net-attach-def -A` |
| A NAD's config | `oc get net-attach-def <nad> -n <ns> -o jsonpath='{.spec.config}' \| python3 -m json.tool` |

## MTV

| TASK | COMMAND |
|---|---|
| Everything | `oc get providers,networkmap,storagemap,plan,migration -n openshift-mtv` |
| Why not Ready | `oc get <kind> <name> -n openshift-mtv -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.message}{"\n"}{end}'` |
| Pipeline | `oc get plan <plan> -n openshift-mtv -o jsonpath='{range .status.migration.vms[*].pipeline[*]}{.name} {.phase} {.progress.completed}/{.progress.total}{"\n"}{end}'` |
| Start | `oc create -f migration.yaml` (new name each run) |
| Add VDDK | `oc patch provider <p> -n openshift-mtv --type merge -p '{"spec":{"settings":{"vddkInitImage":"<image>"}}}'` |
| Inventory log | `oc logs deploy/forklift-controller -c inventory -n openshift-mtv --tail=50` |

## Bastion (Linux)

| TASK | COMMAND |
|---|---|
| OS and kernel | `cat /etc/os-release; uname -r` |
| IPs, routes, DNS | `ip -br addr; ip route; cat /etc/resolv.conf` |
| Which route for an IP | `ip route get <ip>` |
| DNS lookup | `dig +short <name>` |
| Port test | `timeout 4 bash -c "</dev/tcp/<host>/<port>" && echo open` |
| Disks | `lsblk -o NAME,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINTS; df -h` |
| NFS exports | `sudo exportfs -v; showmount -e localhost` |
| Firewall | `sudo firewall-cmd --list-services` |
| Time | `chronyc sources` |

# Appendix B · Exam Map

| EXAM | WHAT IT IS | WHERE IN THIS BOOK |
|---|---|---|
| **DO180** (Red Hat OpenShift Administration I) | Course: `oc`, projects, Pods, Deployments, Routes, storage basics | Ch 3 to 5 (as foundation) |
| **EX188 / DO188** (Containers and Podman) | Exam: build, run and manage containers with Podman | Ch 8 (VDDK image build with podman) |
| **DO280 / EX280** (OpenShift Administrator) | Exam: identity providers, RBAC, operators, networking, storage, cluster health | Ch 3, 4, 5, 6 (operators), 10 |
| **DO316 / EX316** (OpenShift Virtualization Specialist) | Exam: install Virtualization, VMs, disks, networks, live migration, import/migrate VMs | Ch 6, 7, 8, 9 |
| **EX380 / EX370 / EX430 / EX432 / EX267** | Automation, ODF, ACS, ACM, OpenShift AI | Not in this edition |

> **NOTE** This edition covers the migration path only. The same format can carry the full exam track (one chapter per DO180, DO280, EX188, EX288, EX316, EX370, EX380, EX430, EX432, EX267 objective set) captured on the same cluster.
