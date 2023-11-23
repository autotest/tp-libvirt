from provider.sriov import sriov_base

from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Test alias support for iommu device
    """
    def check_xml(vm, exp_alias, iommu_pci_addr):
        """
        Check iommu device's xml

        :param vm: The VM object
        :param exp_alias: The expected alias
        :param iommu_has_addr: Whether the iommu device should have 'address'
        """
        cur_iommu = vm_xml.VMXML.new_from_dumpxml(vm.name)\
            .devices.by_device_tag("iommu")
        if not cur_iommu:
            test.fail("Unable to get iommu device!")
        iommu_attrs = cur_iommu[0].fetch_attrs()
        test.log.debug("iommu_attrs: %s", iommu_attrs)

        if iommu_attrs['alias']['name'] != exp_alias:
            test.fail("Got incorrect 'alias', it should be {}, but got {}!"
                      .format(iommu_attrs['alias']['name'], exp_alias))
        found = True if iommu_attrs.get("address") else False
        if iommu_pci_addr != found:
            test.fail("Got incorrect 'address', the iommu device should{} have"
                      "address.".format('' if iommu_pci_addr else ' not'))

    alias = eval(params.get("alias", "{}"))
    iommu_dict = eval(params.get('iommu_dict', '{}'))
    if alias:
        iommu_dict.update({'alias': alias})
        exp_alias = alias['name']
    else:
        exp_alias = "iommu0"
    iommu_has_addr = "yes" == params.get("iommu_has_addr", "no")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)

    try:
        sriov_test_obj.setup_iommu_test(iommu_dict=iommu_dict)
        vm.start()
        check_xml(vm, exp_alias, iommu_has_addr)
    finally:
        sriov_test_obj.teardown_default()
