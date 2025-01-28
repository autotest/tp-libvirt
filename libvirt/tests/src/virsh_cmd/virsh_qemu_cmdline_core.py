import subprocess

def run(test, params, env):
    
    qemu_binary = params.get("qemu_binary")
    errors = ["tcg_region_init", "assertion failed", "Aborted", \
              "core dumped", "Invalid CPU topology"]

    def check_qemu_cmdline(cpus=9999):

        print("checking with %d CPUs" % cpus)

        command = "%s -accel tcg -smp 10,maxcpus=%d" % (qemu_binary, cpus)
        global output
        output = subprocess.getoutput(command)

        for err in errors:
            if err in output:
                return True
        return False

    cpus = ["9000", "123", "97865", "56789", "123456789"]

    for cpu in cpus:
        failed = check_qemu_cmdline(int(cpu))
        if failed:
            break

    if failed:
        test.fail(output)
