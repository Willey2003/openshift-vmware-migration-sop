# VMware vSphere to OpenShift Virtualization: Migration SOP

A standard operating procedure for moving virtual machines from VMware vSphere to Red Hat OpenShift Virtualization with the Migration Toolkit for Virtualization (MTV). It starts from the basics for an infrastructure engineer new to Linux and Kubernetes, and goes up to production planning for FC, iSCSI and NFS storage. Every step has a web console (GUI) path and a command line (`oc`) path, with real output captured from a lab cluster.

**Read it:** [SOP.md](SOP.md) (renders on GitHub), or download [SOP.html](SOP.html) or [SOP.docx](SOP.docx).

**In a hurry?** [QUICKSTART.md](QUICKSTART.md) is the one-sitting version: connect to OpenShift with the web console and `oc`, then migrate one VM from vCenter, GUI and CLI side by side ([HTML](QUICKSTART.html), [DOCX](QUICKSTART.docx)).

## Validated versions

| Component | Version |
|---|---|
| OpenShift Container Platform | 4.22.15 (Kubernetes 1.35.6) |
| OpenShift Virtualization | 4.22.9 |
| Migration Toolkit for Virtualization | 2.12.9 |
| Kubernetes NMState Operator | 4.22 |
| Source | VMware vSphere 8 |

## Contents

- Part A: Foundations. How a migration works, the lab and production architecture, and the Linux, networking and `oc` basics you need.
- Part B: Platform. Health checks, storage (CSI, StorageClass, RWX), VM networking (NMState, NADs, VLANs), and installing the operators.
- Part C: Migration. vCenter service account, VDDK, source VM preparation, provider, maps, plan, cold and warm migration, validation, rollback, and a worked example.
- Part D: Production. FC, iSCSI and NFS storage, storage copy offload, wave planning, the runbook, troubleshooting, and review questions.

## Repository layout

| Path | Purpose |
|---|---|
| `src/*.md` | Master copy of the SOP. Edit these. |
| `quickstart-src/*.md` | Master copy of the quick start |
| `build.py` | Builds `SOP.md`, `SOP.html` and `SOP.docx` from `src/` (needs pandoc: `pip install pypandoc_binary`) |
| `template.html` | Page layout for the HTML build |
| `images/` | Screenshots. Save a capture as `images/S-nn.png` and the build places it in its placeholder. |

## Rebuild

```bash
pip install pypandoc_binary
python3 build.py                                  # SOP
python3 build.py quickstart-src QUICKSTART        # quick start
```
