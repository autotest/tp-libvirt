import os
import platform

from virttest import data_dir
from virttest import virsh
from virttest.libvirt_xml import vm_xml

from provider.migration import base_steps


def run(test, params, env):
    """
    Verify that migration can succeed when cpu mode is configured.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def get_host_cpu_model():
        """Get host CPU model in lowercase"""
        try:
            # virsh.capabilities() returns XML string directly
            caps_xml = virsh.capabilities()
            import re
            match = re.search(r'<model>(\w+)</model>', caps_xml)
            if match:
                model = match.group(1).lower()
                test.log.info(f"Detected CPU model: {model}")
                return model
        except Exception as e:
            test.log.warning(f"Failed to detect CPU model: {e}")
        return None

    def setup_test():
        """
        Setup steps

        """
        cpu_mode = params.get("cpu_mode")
        migration_option = params.get("migration_option")

        test.log.info("Setup steps.")
        migration_obj.setup_connection()
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        if cpu_mode == "host_model":
            vm_attrs = eval(params.get('vm_attrs', '{}'))
            arch = platform.machine()
            if arch in ['ppc64', 'ppc64le'] and 'cpu' in vm_attrs:
                host_model = get_host_cpu_model()
                if host_model:
                    vm_attrs['cpu']['model'] = host_model
                    vm_attrs['cpu']['fallback'] = params.get('model_fallback', 'forbid')
                    test.log.info(f"Power arch - added CPU model: {host_model}")
            vmxml.setup_attrs(**vm_attrs)
            vmxml.sync()
        else:
            base_steps.sync_cpu_for_mig(params)

        if not vm.is_alive():
            vm.start()
            vm.wait_for_login().close()

        if migration_option == "with_xml":
            xmlfile = os.path.join(data_dir.get_tmp_dir(), '%s.xml' % vm_name)
            virsh.dumpxml(vm_name, extra="--migratable", to_file=xmlfile, ignore_status=False)
            params.update({"virsh_migrate_extra": f"--xml {xmlfile}"})

    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        migration_obj.cleanup_connection()
