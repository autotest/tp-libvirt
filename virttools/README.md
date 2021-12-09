The test type 'virttools' is meant to cover test cases for the tools in the
virt-manager repository, e.g. virt-xml, virt-clone, virt-install.

By using avocado-vt we obtain access to many existing test functions for libvirt
and qemu. Please, note the related commit in avocado-vt to allow for the new
test type.

'virttools' uses the same basic setup as the tp-libvirt/libvirt test type assuming
most tests will suppose there's at least one existing vm 'avocado-vt-vm1' with
image in the default location (avocado-vt/.../images/jeos-27-s390x.qcow2), e.g.
for 'virt-xml avocado-vt-vm1 --add-device...', 'virt-clone avocado-vt-vm1'.
