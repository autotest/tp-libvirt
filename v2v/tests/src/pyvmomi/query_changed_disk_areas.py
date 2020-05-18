import logging

from virttest.utils_pyvmomi import VSphere, vim
from virttest.remote import wait_for_login
from virttest.utils_misc import wait_for


def run(test, params, env):
    """
    Basic pyvmomi test

    1) create a snapshot
    2) power on the VM, write some fixed length data to
       the second disk.
    3) power off the VM, query the changes and compare
       the lenght of changed area.
    """

    def safe_power_off(conn):
        """
        Power off safely

        If the VM is poweroff state, the power_off call
        will fail, this function checks the state before
        power off operation.
        """
        power_state = conn.get_vm_summary()['power_state']
        if power_state != 'poweredOff':
            conn.power_off()

    vm_name = params.get("main_vm")
    if not vm_name:
        test.error('No VM specified')

    # vsphere server's host name or IP address
    vsphere_host = params.get("vsphere_host")
    # vsphere user
    vsphere_user = params.get("vsphere_user")
    # vsphere password
    vsphere_pwd = params.get("vsphere_pwd")

    # vm boots up timeout value, default is 5 mins
    vm_bootup_timeout = params.get("vm_bootup_timeout ", 300)
    # vm user
    vm_user = params.get("vm_user", 'root')
    # vm password
    vm_pwd = params.get("vm_pwd")

    # vm remote login client arguments setting
    vm_client = params.get("vm_client", 'ssh')
    vm_port = params.get("vm_port", 22)
    vm_prompt = params.get("vm_prompt", r"[\#\$\[\]%]")

    try:
        connect_args = {
            'host': vsphere_host,
            'user': vsphere_user,
            'pwd': vsphere_pwd}
        conn = VSphere(**connect_args)
        conn.target_vm = vm_name

        # Poweroff the guest first if it is Up.
        safe_power_off(conn)
        # Remove all snapshots first
        conn.remove_all_snapshots()

        # Get disk counts of the VM
        if len(
            conn.get_hardware_devices(
                dev_type=vim.vm.device.VirtualDisk)) < 2:
            test.error('The main_vm must have at least two disks')

        # Create a snapshot
        conn.create_snapshot()
        # Poweron the guest
        conn.power_on()
        # Wait for VM totally boots up to get the IP address
        vm_ipaddr = wait_for(
            lambda: conn.get_vm_summary()['ip_address'],
            vm_bootup_timeout)
        if not vm_ipaddr:
            test.fail('Get VM IP address failed')

        logging.info("VM's (%s) IP address is %s", vm_name, vm_ipaddr)

        conn_kwargs = {
            'client': vm_client,
            'host': vm_ipaddr,
            'port': vm_port,
            'username': vm_user,
            'password': vm_pwd,
            'prompt': vm_prompt}

        vm_session = wait_for_login(**conn_kwargs)

        # Write some fixed length of data
        cmd = 'dd if=/dev/urandom of=/dev/sdb bs=6000 count=10000 seek=1000'
        res = vm_session.cmd_output(cmd)
        logging.debug('Session outputs:\n%s', res)

        # Power off at once
        conn.power_off()

        # Query the changed area
        disk_change_info = conn.query_changed_disk_areas(
            disk_label='Hard disk 2')
        if not disk_change_info.changedArea:
            test.fail('Not found any changes')

        total = 0
        for change in disk_change_info.changedArea:
            total += change.length

        logging.info('total change length is %s', total)
        if not 60000000 <= total <= 61000000:
            test.fail('Unexpected change size')

    finally:
        safe_power_off(conn)
        # Remove all snapshots first
        conn.remove_all_snapshots()
        conn.close()
