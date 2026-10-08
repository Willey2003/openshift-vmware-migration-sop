# Chapter 10 · Troubleshooting and Going to Production

## The troubleshooting order

When something does not work, go from the platform down to the object, and read before you change.

```bash
oc get co | awk 'NR==1 || $3!="True" || $5!="False"'            # 1. is the platform healthy?
oc get nodes; oc adm top nodes                                     # 2. are the nodes?
oc get csv -A | grep -v Succeeded                                  # 3. are the operators?
oc get hco -n openshift-cnv -o jsonpath='{.status.conditions[?(@.type=="Degraded")].status}'
oc get providers,networkmap,storagemap,plan -n openshift-mtv       # 4. MTV objects Ready?
oc describe plan <plan> -n openshift-mtv | sed -n '/Conditions/,$p' # 5. why not?
oc get pods,pvc,events -n <target-project> --sort-by=.lastTimestamp # 6. what happened in the target
oc logs <conversion-pod> -n <target-project> --tail=50             # 7. the conversion itself
```

## Symptom table

| SYMPTOM | LIKELY CAUSE | CHECK | FIX |
|---|---|---|---|
| Provider not Ready, *ConnectionTestSucceeded=False* | Wrong password, DNS, or 443 blocked | `oc describe provider`; from a node: `curl -k https://<vcenter>/sdk` | Fix the Secret, DNS or firewall |
| Provider stuck loading inventory | Very large vCenter or account cannot see objects | `oc logs deploy/forklift-controller -c inventory -n openshift-mtv` | Grant the role on the right objects, wait |
| Plan not Ready: *network not mapped* / *storage not mapped* | A NIC's port group or a disk's datastore missing from the map | Plan conditions | Add the mapping |
| Plan warning: *VM has snapshots* | Existing snapshots on the source | Validation concerns column | Delete/consolidate snapshots |
| `DiskTransfer` very slow | No VDDK; or 902 blocked to the ESXi host | Provider `settings`; port test from Chapter 2 | Add VDDK; open 902 |
| `ImageConversion` failed | Unsupported guest OS, encrypted disk, BitLocker | Conversion Pod log | Fix the guest (suspend BitLocker, upgrade OS) or `skipGuestConversion` for KVM-ready guests |
| VM created but will not schedule | No node with KVM, or NAD bridge missing on nodes | `oc describe vmi`; `oc get nodes -o custom-columns=...kvm` | Expose hardware virtualization; fix NNCP |
| VM boots, no network | VLAN ID wrong, trunk/promiscuous settings (nested), DHCP missing | NNCE status, NAD `vlan`, vSphere port group | Correct VLAN / port group security / static IP |
| VM not live-migratable | RWO disk | `oc get vm -o jsonpath='{.status.conditions}'` → `LiveMigratable=False` | StorageMap RWX; StorageProfile |
| `oc debug node` fails with *namespace ... not found* | Context points to a deleted project | `oc project` | `oc project default` or `--to-namespace=default` |
| Node stunned, etcd alarms after vSphere changes | ISO or NIC changed on a running node | Node journal gap | Change node VM hardware only when powered off |

## Support data

```bash
# Reference: platform must-gather, and the MTV/Virtualization ones (the images come from the installed CSVs)
oc adm must-gather --dest-dir=~/mg-platform
oc adm must-gather --dest-dir=~/mg-cnv \
  --image=$(oc get csv -n openshift-cnv -o jsonpath='{.items[0].spec.relatedImages[?(@.name=="must-gather")].image}')
oc adm must-gather --dest-dir=~/mg-mtv \
  --image=$(oc get csv -n openshift-mtv -o jsonpath='{.items[0].spec.relatedImages[?(@.name=="must_gather")].image}')
```

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get csv -n openshift-mtv -o jsonpath="{.items[0].spec.relatedImages[*].name}" | tr " " "\n" | grep -i must
must_gather
```

The MTV operator ships its own must-gather image, named `must_gather` (underscore) in its CSV. Querying the CSV means you always get the image matching the installed version, which is what Red Hat support will ask for.

## From lab to production

| AREA | THIS LAB | PRODUCTION |
|---|---|---|
| Nodes | 3 nested VMs, compact | 3 control plane + bare-metal workers sized like ESXi hosts |
| Storage | One NFS VM, `nfs-csi` | Array vendor CSI or ODF, block, RWX, snapshots; storage copy offload where supported |
| Networks | linux bridge on one NIC | Bonded NICs, one NAD per VLAN, dedicated migration and transfer networks |
| Identity | htpasswd `ocpadmin` | AD/LDAP or OIDC, group-based RBAC |
| vCenter account | Propagated on the root | Scoped to migrated folders, password in a vault |
| TLS | `insecureSkipVerify` | vCenter CA trusted |
| Disk transfer | No VDDK | VDDK always; warm migration for large VMs |
| Process | One test VM | Waves: pilot (non-critical) → wave per application, each with a change record, baseline, cutover window, validation, rollback |
| Backup | None | OADP (Velero) for VMs and their PVCs, tested restore before wave 1 |

### A wave, step by step

1. **Discover**: list VMs per application (RVTools or the MTV inventory), record OS, disks, NICs, IPs, dependencies.
2. **Prepare**: remove snapshots, enable CBT (warm), suspend BitLocker, record the baseline (IP, MAC, services).
3. **Plan**: one Plan per application or wave, warm for anything large, `targetPowerState: on`.
4. **Pre-copy** (warm): start the migration days ahead; MTV copies deltas on a schedule (`controller_precopy_interval`, 60 minutes by default).
5. **Cutover**: in the change window, set the cutover time; MTV powers the source off, copies the last delta, converts, starts the VM.
6. **Validate**: the table in 9.8, plus the application owner's test.
7. **Decide**: keep, or roll back by powering the source on. Delete source VMs only after the agreed soak period.

> **EXAM TIP** Red Hat exams reward reading status, not guessing. For every task, finish with a `get` and a `describe` that prove the result, exactly like the real captures in this book.

> **LAB — DO IT ON THE BASTION** Break and fix, one at a time, on a scratch plan (never on the provider other people use): (1) remove the datastore from a copy of the StorageMap and read the plan error; (2) point a NAD at VLAN 9999 and boot a VM on it; (3) set your context to a project you then delete, and reproduce the `oc debug` error from Chapter 4.

## Check yourself

**Q1. In what order do you look when "the migration failed"?**
Platform (ClusterOperators, nodes), operators (CSV, HCO), MTV objects (provider, maps, plan conditions), then the target project (Pods, PVCs, events, conversion log). *Logic:* each layer depends on the one above; a failure higher up explains everything below it.

**Q2. Warm migration: what makes the cutover short?**
Most of the data is copied while the source runs; at cutover only the last delta (changed blocks via CBT) is copied before conversion. *Logic:* downtime = final delta + conversion + boot, not the full disk copy.

**Q3. Why collect must-gather with the image from the installed CSV instead of `latest`?**
It must match the operator version that produced the problem. *Logic:* support compares your data with that version's code and known issues.
