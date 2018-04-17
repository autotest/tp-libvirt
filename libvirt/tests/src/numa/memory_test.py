import logging
import re
import time
from virttest import virt_vm


def run(test, params, env):
    numa_maps_before = numa_maps_after = {}

    def get_host_pages_to_guest(vmpid, guest_addr):
        numa_maps = {}
        numa_maps_fd = open('/proc/%s/numa_maps' % vmpid, 'rt')
        numa_maps_info = numa_maps_fd.readlines()
        numa_maps_fd.close()
        for line in numa_maps_info:
            if re.match('^%s.default.anon.*' % guest_addr, line):
                logging.debug(
                    "Found guest memory deatils in host : %s" %
                    line)
                for data in (line.strip('\n')).split(' '):
                    if '=' in data:
                        numa_maps[data.split('=')[0]] = data.split('=')[1]
        logging.debug("Numa_maps dict = %s", numa_maps)
        return numa_maps

    vm_name = params.get("main_vm")
    mem_allocate = int(params.get("mem_allocate"))
    logging.debug("Memory to be allocated is : %s" % mem_allocate)
    vm = env.get_vm(vm_name)
    if vm.is_alive():
        vm.destroy()
    try:
        vm.start()
    except virt_vm.VMStartError, detail:
        test.fail("Test failed in positive case.\n "
                  "error: %s\n%s" % detail)
    logging.debug("VM = %s" % vm)
    vmpid = vm.get_pid()
    logging.debug("guest pid = %s" % vmpid)
    fd = open("/proc/%s/cmdline" % vmpid, 'rt')
    cmd = fd.read()
    guest_memory = re.search('.-m.([0-9]+).-', cmd)
    guest_memory = int(guest_memory.group(1)) * 1024
    logging.debug("Guest vm Memory = %s" % guest_memory)
    logging.debug("Get the Guest Pysical Memory Address")
    # Read data from /proc/<pid>/smaps
    smaps = open('/proc/%s/smaps' % vmpid)
    smaps_info = smaps.readlines()
    guest_addr = []
    size_pattern = '^Size:.*%s.*kB' % guest_memory
    for lineNo, lineInfo in enumerate(smaps_info):
        if re.match(size_pattern, lineInfo):
            guest_addr.append(
                re.search('(^[0-9a-f]*)-', smaps_info[lineNo - 1]).group(1))
    logging.debug("Guest Pysical Address = %s" % guest_addr)
    numa_maps_before = get_host_pages_to_guest(vmpid, guest_addr[0])
    logging.debug("vm.verify_alive=%s", vm.is_alive())
    if vm.is_alive():
        session = vm.wait_for_login(timeout=600)
        cmd = "cd /root/; rm -rf pmemory-test; git clone http://9.40.192.92:81/\
              gits/pmemory-test/; \
              cd /root/pmemory-test; gcc mem_numa.c -o mem_numa -lnuma -w"
        rc = session.sendline(cmd)
        logging.debug("Git clone and Compilation retrun code %s", rc)
        cmd = ("cd /root/pmemory-test; ./mem_numa %s &" % mem_allocate)
        logging.debug("command : %s" % cmd)
        rc = session.sendline(cmd)
        logging.debug("test retrun code %s", rc)
    time.sleep(20)
    numa_maps_after = get_host_pages_to_guest(vmpid, guest_addr[0])
    diff_mem = (int(numa_maps_after['anon']) -
                int(numa_maps_before['anon'])) * 64
    logging.debug("diff_mem = %s :: mem_allocate = %s" %
                  (diff_mem, mem_allocate))
    if diff_mem < mem_allocate:
        test.fail("Not allocated as requested")
