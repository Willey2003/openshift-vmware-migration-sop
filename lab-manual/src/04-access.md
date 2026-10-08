# Chapter 4 · Day 1: Logging In, Users and Access

The installer leaves you two ways in: a **kubeconfig** file with a certificate for `system:admin`, and a temporary user `kubeadmin` with a random password. Both are "break glass" credentials. Day 1 is replacing them with a real identity provider and a named admin.

## Who am I, and where?

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc whoami; oc whoami --show-server; oc whoami --show-console
system:admin
https://api.ocp1.example.com:6443
https://console-openshift-console.apps.ocp1.example.com
```

`system:admin` means `oc` is using the installer's certificate from `~/ocp1/auth/kubeconfig` (exported as `KUBECONFIG`). That file is the root password of the cluster: never copy it to a laptop, never commit it.

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc config get-contexts
CURRENT   NAME                                                           CLUSTER                                  AUTHINFO                                              NAMESPACE
          admin                                                          ocp1                           admin                                                 
*         nfs-test/api-ocp1-example-com:6443/system:admin   api-ocp1-example-com:6443   system:admin/api-ocp1-example-com:6443   nfs-test
```

A **context** = cluster + user + current project. The `*` marks the active one, and its project is `nfs-test`.

> **GOTCHA — REAL ISSUE** `nfs-test` was a scratch project used to test NFS, and it was deleted afterwards. The context still pointed at it, and that broke a command you would never suspect. While capturing this book, `oc debug node/ocp-node-1` failed with `error: unable to get namespace namespaces "nfs-test" not found`, because `oc debug` creates its temporary Pod in the *current* project. Two fixes: `oc project default` (switch the context to a project that exists) or `oc debug node/ocp-node-1 --to-namespace=default` (used for every node capture in this book). Rule: after deleting a project, run `oc project` and move off it.

## Add a real login: HTPasswd

OpenShift does not store users itself. It trusts an **identity provider** (LDAP/Active Directory, OpenID Connect, HTPasswd...). HTPasswd is a file of user names and hashed passwords kept in a Secret: perfect for a lab, and an EX280 objective.

```bash
# Reference: ~/ocp-install/add-htpasswd-admin.sh (prompts for the password, stores only a bcrypt hash)
htpasswd -c -B -b "$F" ocpadmin "$P1"
oc create secret generic htpass-secret --from-file=htpasswd="$F" -n openshift-config --dry-run=client -o yaml | oc apply -f -
oc apply -f - <<'Y'
apiVersion: config.openshift.io/v1
kind: OAuth
metadata: {name: cluster}
spec:
  identityProviders:
  - name: lab-htpasswd
    mappingMethod: claim
    type: HTPasswd
    htpasswd: {fileData: {name: htpass-secret}}
Y
oc adm policy add-cluster-role-to-user cluster-admin ocpadmin
oc -n openshift-authentication rollout status deploy/oauth-openshift --timeout=300s
```

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get oauth cluster -o jsonpath="{range .spec.identityProviders[*]}{.name}{\"\t\"}{.type}{\"\n\"}{end}"; oc get users; oc get identity
lab-htpasswd	HTPasswd
NAME       UID                                    FULL NAME   IDENTITIES
ocpadmin   88970b2d-e4e2-49c8-ab06-a18dba58920a               lab-htpasswd:ocpadmin
NAME                    IDP NAME       IDP USER NAME   USER NAME   USER UID
lab-htpasswd:ocpadmin   lab-htpasswd   ocpadmin        ocpadmin    88970b2d-e4e2-49c8-ab06-a18dba58920a
```

Three objects, three ideas:

| OBJECT | WHAT IT IS | CREATED WHEN |
|---|---|---|
| OAuth `cluster` → identity provider `lab-htpasswd` | Where passwords are checked | When you apply the OAuth config |
| Identity `lab-htpasswd:ocpadmin` | "This login at this provider" | At the **first successful login** |
| User `ocpadmin` | The OpenShift account that RBAC refers to | At the first successful login (`mappingMethod: claim`) |

> **NOTE** A user does not appear in `oc get users` until it has logged in once. If you grant a role before the first login, the binding is created anyway and starts working when the user appears.

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get clusterrolebinding -o wide | grep -E "NAME|ocpadmin"
NAME                                                                        ROLE                                                                                    AGE   USERS                                                            GROUPS                                         SERVICEACCOUNTS
cluster-admin-0                                                             ClusterRole/cluster-admin                                                               8h    ocpadmin                                                                                                        
```

`oc adm policy add-cluster-role-to-user` created the ClusterRoleBinding `cluster-admin-0`: user `ocpadmin` holds the `cluster-admin` ClusterRole everywhere. In vSphere words: a permission with the *Administrator* role on the vCenter root, propagated.

## Remove kubeadmin

Only after `ocpadmin` has logged in and proved it is cluster-admin:

```bash
# Reference
oc login -u ocpadmin https://api.ocp1.example.com:6443
oc auth can-i '*' '*' --all-namespaces            # must print: yes
oc delete secret kubeadmin -n kube-system
```

**admin@bastion — real capture**

```console
[admin@bastion ~]$ oc get secret kubeadmin -n kube-system
Error from server (NotFound): secrets "kubeadmin" not found
```

An error is the correct result here: the secret is gone, so the `kubeadmin` login no longer exists. The kubeconfig certificate still works as break-glass access.

> **SCREENSHOT S-03:** Web console login page showing the `lab-htpasswd` button (and no `kube:admin` button any more).

> **GOTCHA — REAL ISSUE** If you delete `kubeadmin` before another cluster-admin works, the only way back in is the installer's kubeconfig. If that file is also lost, you have lost the cluster. Keep `~/ocp1/auth/kubeconfig` backed up somewhere safe and offline.

## RBAC in one table

| NEED | COMMAND |
|---|---|
| Make someone admin of one project | `oc adm policy add-role-to-user admin alice -n mtv-test` |
| Read-only on one project | `oc adm policy add-role-to-user view bob -n mtv-test` |
| Cluster-wide admin | `oc adm policy add-cluster-role-to-user cluster-admin ocpadmin` |
| Group | `oc adm groups new vm-admins alice bob` then `oc adm policy add-role-to-group admin vm-admins -n mtv-test` |
| Can I...? | `oc auth can-i create virtualmachines -n mtv-test` |
| Can she...? | `oc auth can-i create virtualmachines -n mtv-test --as alice` |
| Stop everyone creating projects | `oc adm policy remove-cluster-role-from-group self-provisioner system:authenticated:oauth` |

> **PRODUCTION** Use your company identity provider (Active Directory via LDAP, or Entra ID / Keycloak via OIDC), sync AD groups with `oc adm groups sync`, and grant roles to groups, never to individuals. OpenShift Virtualization adds the ClusterRoles `kubevirt.io:admin`, `kubevirt.io:edit` and `kubevirt.io:view`, aggregated into `admin`, `edit` and `view`, so project admins can manage VMs out of the box.

> **EXAM TIP** HTPasswd, users, groups, `oc adm policy`, removing `kubeadmin` and `self-provisioner` are core EX280 objectives. Practise the whole sequence in under ten minutes, including waiting for the `oauth-openshift` Pods to roll.

> **LAB — DO IT ON THE BASTION** Add a second htpasswd user `vmviewer` (extract the current file first with `oc extract secret/htpass-secret -n openshift-config --to=- > users.htpasswd`, add the user with `htpasswd -B -b`, then replace the secret). Grant `view` on `mtv-test`. Log in as `vmviewer` in a private browser window and confirm you can see the migrated VM but cannot start it.

## Check yourself

**Q1. You ran `oc get users` right after applying the OAuth config and `ocpadmin` is missing. Is the config broken?**
No. *Logic:* User and Identity objects are created on first login (`mappingMethod: claim`). Log in once, then look again.

**Q2. Why must you test `ocpadmin` before deleting the `kubeadmin` secret?**
Because `kubeadmin` is then gone for good. *Logic:* if the new identity provider is misconfigured, only the installer kubeconfig remains. Prove the replacement before removing the original.

**Q3. `oc debug node/...` fails with "namespaces nfs-test not found", but you are cluster-admin. Why?**
Your current context points at a deleted project and `oc debug` creates its Pod there. *Logic:* permissions are fine; the target namespace does not exist. Use `oc project default` or `--to-namespace=default`.

**Q4. Role vs ClusterRole, RoleBinding vs ClusterRoleBinding: which pair gives "admin in project mtv-test only"?**
The ClusterRole `admin` bound with a **RoleBinding** in `mtv-test`. *Logic:* the role defines *what*, the binding defines *where*. A RoleBinding limits even a ClusterRole to its own namespace.
