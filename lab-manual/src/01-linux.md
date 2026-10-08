# Chapter 1 · Linux for the Migration Engineer (RHEL 10)

Every OpenShift node is a Linux box, every migrated VM disk lands in a Linux filesystem, and every `oc` command you type runs in a Linux shell. You do not need to become a Linux administrator to migrate VMs, but you need to be comfortable on the **bastion**: the RHEL 10 VM that holds the installer, the `oc` client, the cluster credentials and the NFS share.

Think of the bastion as the Windows jump host with PowerCLI you already use for vSphere. It is where you stand when you talk to the cluster.

## Know the box you are on

Before touching anything, identify the distribution, the kernel and who you are.

**admin@bastion — real capture**

```console
[admin@bastion ~]$ cat /etc/os-release | head -4; uname -r
NAME="Red Hat Enterprise Linux"
VERSION="10.0 (Coughlan)"
ID="rhel"
ID_LIKE="centos fedora"
6.12.0-55.9.1.el10_0.x86_64
```

**admin@bastion — real capture**

```console
[admin@bastion ~]$ whoami; id; hostname; nproc; free -h | head -2
admin
uid=1000(admin) gid=1000(admin) groups=1000(admin),10(wheel) context=unconfined_u:unconfined_r:unconfined_t:s0-s0:c0.c1023
bastion.example.com
8
               total        used        free      shared  buff/cache   available
Mem:            15Gi       6.1Gi       224Mi       348Mi       9.7Gi       9.3Gi
```

How to read it:

- `ID="rhel"` with `VERSION="10.0"` tells you the package manager is `dnf`, the firewall is `firewalld`, and SELinux is on.
- `groups=...10(wheel)` means you may use `sudo`. Membership of `wheel` is the Linux version of being in the vCenter *Administrators* group.
- `context=unconfined_u:...` is your **SELinux** label. You will meet SELinux again on the nodes: it is why a container cannot read a file just because the Unix permissions allow it.
- `free` shows only 224 MiB "free" but 9.3 GiB "available". Linux uses spare RAM as file cache (`buff/cache`) and gives it back on demand. **Available** is the number that matters, the same way you read *consumed* vs *active* memory on an ESXi host.

## Disks, partitions and filesystems

**admin@bastion — real capture**

```console
[admin@bastion ~]$ lsblk -o NAME,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINTS
NAME             SIZE TYPE FSTYPE      LABEL                   MOUNTPOINTS
sda                1T disk                                     
├─sda1           600M part vfat                                /boot/efi
├─sda2             1G part xfs                                 /boot
└─sda3        1022.4G part LVM2_member                         
  ├─rhel-root     70G lvm  xfs                                 /
  ├─rhel-swap    7.9G lvm  swap                                [SWAP]
  └─rhel-home  944.5G lvm  xfs                                 /home
sdb              500G disk xfs         ocpnfs                  /exports/ocp
sr0              7.9G rom  iso9660     RHEL-10-0-BaseOS-x86_64 /mnt/rhel10dvd
```

| LINUX | VSPHERE EQUIVALENT | IN THIS LAB |
|---|---|---|
| `sda`, `sdb` (disk) | A virtual disk (VMDK) attached to the VM | `sda` = 1 TB OS disk, `sdb` = 500 GB NFS disk |
| `sda1` (partition) | A slice of that disk | EFI and `/boot` |
| LVM volume group `rhel` | A datastore spanning one or more disks | Holds `root`, `swap`, `home` |
| LVM logical volume `rhel-root` | A VMDK carved from the datastore | 70 GB root filesystem |
| `xfs` (filesystem) | VMFS, but inside the guest | All data filesystems |
| Mount point `/exports/ocp` | A drive letter, except it is a folder | Where the NFS disk appears |
| `sr0` | The VM's CD/DVD drive | RHEL 10 DVD, used as the local package repository |

**admin@bastion — real capture**

```console
[admin@bastion ~]$ df -h /exports/ocp
Filesystem      Size  Used Avail Use% Mounted on
/dev/sdb        500G   30G  470G   6% /exports/ocp
```

The 30 GB already used is the boot-source images OpenShift Virtualization downloaded (Chapter 6) plus the migrated Ubuntu disk (Chapter 9).

```bash
# Reference: how the 500 GB disk became /exports/ocp (run once, after adding the disk in vCenter)
sudo mkfs.xfs -L ocpnfs /dev/sdb
sudo mkdir -p /exports/ocp
echo 'LABEL=ocpnfs /exports/ocp xfs defaults 0 0' | sudo tee -a /etc/fstab
sudo systemctl daemon-reload && sudo mount -a
```

> **TIP** Mount by `LABEL=` (or `UUID=`), never by `/dev/sdb`. Device names can change order when you add a disk in vCenter; the label travels with the filesystem.

## Packages without a subscription

The bastion is not registered with Red Hat, so `dnf` installs from the RHEL 10 DVD mounted at `/mnt/rhel10dvd` (the `sr0` line above).

```bash
# Reference: local repository from the DVD
sudo mkdir -p /mnt/rhel10dvd && sudo mount /dev/sr0 /mnt/rhel10dvd
sudo tee /etc/yum.repos.d/rhel10-dvd.repo <<'EOF'
[dvd-BaseOS]
name=RHEL 10 DVD BaseOS
baseurl=file:///mnt/rhel10dvd/BaseOS
gpgcheck=0
[dvd-AppStream]
name=RHEL 10 DVD AppStream
baseurl=file:///mnt/rhel10dvd/AppStream
gpgcheck=0
EOF
sudo dnf -y install nfs-utils nmstate httpd-tools bind-utils jq
```

## Services, firewall and the NFS server

**admin@bastion — real capture**

```console
[admin@bastion ~]$ sudo firewall-cmd --list-services
cockpit dhcpv6-client mountd nfs rpc-bind ssh
```

`nfs`, `mountd` and `rpc-bind` were opened for the NFS server; `cockpit` is the bastion's own web console on port 9090. A service in Linux (`systemctl status nfs-server`) is the equivalent of a Windows service; `firewalld` is the Windows Defender Firewall.

| NEED | COMMAND |
|---|---|
| Is a service running? | `systemctl status nfs-server` |
| Start now and at every boot | `sudo systemctl enable --now nfs-server` |
| Last 50 log lines of a service | `journalctl -u nfs-server -n 50` |
| Open a firewall service permanently | `sudo firewall-cmd --permanent --add-service=nfs && sudo firewall-cmd --reload` |
| Which ports are listening | `sudo ss -tlnp` |
| Who uses the disk | `sudo du -sh /exports/ocp/* \| sort -h \| tail` |
| Find big files | `find / -xdev -size +1G -type f 2>/dev/null` |
| Follow a log live | `journalctl -f` |

## Text tools you will use against `oc` output

`oc` prints tables. Five Linux tools turn those tables into answers.

```bash
oc get pods -A --no-headers | awk '{print $4}' | sort | uniq -c        # how many Pods in each state
oc get pods -A --no-headers | grep -v -E 'Running|Completed'            # only the unhealthy ones
oc get co --no-headers | awk '$3!="True" || $5!="False"'                # degraded cluster operators
oc get pv --no-headers | awk '{s+=$2} END {print s/1024/1024/1024 " GiB"}' # total PV size
oc get vm -A -o name | xargs -n1 basename                               # just the VM names
```

> **GOTCHA — REAL ISSUE** PowerShell on the Windows jump host rewrites double quotes inside the argument of `ssh host "command"`. A `jsonpath` like `{"\n"}` arrives on the bastion broken. Two fixes were used while building this lab: put the commands in a script on the bastion and run the script, or send it base64-encoded (`echo <base64> | base64 -d | bash`). From inside an SSH session on the bastion the problem does not exist.

> **EXAM TIP** Red Hat exams run on RHEL. You will be expected to use `vi`/`vim`, `grep`, `systemctl`, `journalctl`, `firewall-cmd`, `ss`, `lsblk`, `df` and `dnf` without help. DO180 and EX280 assume this Linux floor; EX316 assumes EX280.

> **LAB — DO IT ON THE BASTION** (1) Find how much space the migrated Ubuntu disk takes under `/exports/ocp` with `sudo du -sh /exports/ocp/*`. (2) Show which process listens on TCP 2049 with `sudo ss -tlnp | grep 2049`. (3) Count Pods per state across the cluster with the `awk | sort | uniq -c` pattern above.

## Check yourself

**Q1. `free -h` shows 224 MiB free. Is the bastion out of memory?**
No. *Logic:* Linux keeps spare RAM as page cache (`buff/cache` 9.7 GiB) and frees it on demand. The real headroom is the **available** column, 9.3 GiB.

**Q2. You add a third disk in vCenter and after a reboot `/exports/ocp` is empty. Why, and how do you prevent it?**
The new disk may have taken the name `sdb` and the NFS disk became `sdc`, so a mount by device name mounted the wrong (empty) disk. *Logic:* device names follow detection order; labels and UUIDs belong to the filesystem. Always mount by `LABEL=` or `UUID=`.

**Q3. Which group membership lets `admin` use `sudo` here, and what is the vSphere analogy?**
`wheel` (gid 10). *Logic:* RHEL's sudoers grants `wheel` full sudo, the same way a vCenter role granted on the root object applies everywhere.

**Q4. A client cannot mount the NFS share, but `systemctl status nfs-server` is active. What is the first thing to check on the bastion?**
The firewall: `sudo firewall-cmd --list-services` must show `nfs` (and `mountd`, `rpc-bind` for NFSv3 clients). *Logic:* a running service is useless if its port is closed; it is the Linux version of "the VM is up but the port group is wrong".
