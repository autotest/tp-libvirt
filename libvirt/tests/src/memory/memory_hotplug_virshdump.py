import os
import time
import logging
import tempfile
from avocado.utils import process
from virttest import virsh
from virttest import utils_hotplug
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.vm_xml import VMXML, VMCPUXML


def run(test, params, env):
    """
    Test memory hotplug with simultaneous virsh dump operations.
    
    :param test: QEMU test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    
    mem_hotplug_iterations = int(params.get("mem_hotplug_iterations", "10"))
    virshdump_iterations = int(params.get("virshdump_iterations", "5"))
    virshdump_delay = int(params.get("virshdump_delay", "10"))
    dump_path = params.get("dump_path", "./virsh_dumps")
    dump_options = params.get("dump_options")
    
    tg_size = params.get("tg_size", "524288")  # 512 MiB in KiB
    tg_node = params.get("tg_node", "0")
    mem_model = params.get("mem_model", "dimm")
    
    failures = []
    ops_log = "/tmp/memory_hotplug_operations.log"

    def run_iterations(vm_name, iterations, dump_delay, dump_dir):
        """
        Both operations must succeed for the iteration to be counted.
        If either fails the iteration is logged as an error and the loop stops.

        :param vm_name:       libvirt domain name
        :param iterations:    number of attach+dump cycles to run
        :param hotplug_delay: seconds to wait after a successful dump before
                              starting the next attach-device (mem_hotplug_delay)
        :param dump_delay:    seconds to wait after attach-device succeeds before
                              issuing virsh dump (virshdump_delay)
        :param dump_dir:      directory to write dump files into
        """
        if not os.path.exists(dump_dir):
            os.makedirs(dump_dir)

        with open(ops_log, "w") as f:
            f.write("Operations started at %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            f.write("=" * 80 + "\n")

        completed = 0
        for i in range(iterations):
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            logging.info("Iteration %d/%d starting at %s", i + 1, iterations, ts)
            with open(ops_log, "a") as f:
                f.write("\n[Iteration %d/%d] %s\n" % (i + 1, iterations, ts))
            if not virsh.is_alive(vm_name):
                msg = "  VM '%s' is not running – aborting iterations" % vm_name
                logging.error(msg)
                with open(ops_log, "a") as f:
                    f.write(msg + "\n")
                failures.append(msg)
                break

            tmp_file = None
            attach_ok = False
            try:
                mem_xml = utils_hotplug.create_mem_xml(
                    tg_size=int(tg_size),
                    tg_sizeunit="KiB",
                    tg_node=int(tg_node),
                    mem_model=mem_model,
                    mem_addr={"slot": str(i)}
                )
                tmp_file = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".xml")
                tmp_file.write(str(mem_xml))
                tmp_file.close()

                logging.info("  [attach-device] slot %d", i)
                attach_result = virsh.attach_device(
                    vm_name, tmp_file.name, flagstr="--live", debug=True)

                if attach_result.exit_status == 0:
                    attach_ok = True
                    logging.info("  [attach-device] SUCCESS")
                    with open(ops_log, "a") as f:
                        f.write("  attach-device slot %d: SUCCESS\n" % i)
                else:
                    stderr = attach_result.stderr.strip()
                    if "domain is not running" in stderr:
                        msg = "  [attach-device] SKIP – domain is not running: %s" % stderr
                        logging.warning(msg)
                        with open(ops_log, "a") as f:
                            f.write(msg + "\n")
                        break
                    else:
                        msg = "  [attach-device] ERROR: %s" % stderr
                        logging.error(msg)
                        with open(ops_log, "a") as f:
                            f.write(msg + "\n")
                        failures.append("Iteration %d attach-device failed: %s" % (i + 1, stderr))
                        break
            except Exception as exc:
                msg = "  [attach-device] EXCEPTION: %s" % exc
                logging.error(msg)
                with open(ops_log, "a") as f:
                    f.write(msg + "\n")
                failures.append("Iteration %d attach-device exception: %s" % (i + 1, exc))
                break
            finally:
                if tmp_file and os.path.exists(tmp_file.name):
                    os.unlink(tmp_file.name)

            if not attach_ok:
                break

            if not virsh.is_alive(vm_name):
                msg = "  VM is not running before dump %d – aborting" % (i + 1)
                logging.warning(msg)
                with open(ops_log, "a") as f:
                    f.write(msg + "\n")
                break

            dump_file = os.path.join(
                dump_dir, "dump_%d_%s" % (i + 1, time.strftime("%Y%m%d_%H%M%S"))
            )
            dump_ok = False
            try:
                logging.info("  [virsh dump] %s", dump_file)
                dump_result = virsh.dump(vm_name, dump_file, dump_options, debug=True)

                if dump_result.exit_status == 0:
                    if os.path.exists(dump_file):
                        dump_size = os.path.getsize(dump_file)
                        dump_ok = True
                        logging.info("  [virsh dump] SUCCESS (%d bytes)", dump_size)
                        with open(ops_log, "a") as f:
                            f.write("  virsh dump %d: SUCCESS (%d bytes)\n" % (i + 1, dump_size))
                    else:
                        msg = "  [virsh dump] ERROR – command succeeded but file not found"
                        logging.error(msg)
                        with open(ops_log, "a") as f:
                            f.write(msg + "\n")
                        failures.append("Iteration %d dump file missing after success" % (i + 1))
                        break
                else:
                    stderr = dump_result.stderr.strip()
                    if "domain is not running" in stderr:
                        msg = "  [virsh dump] SKIP – domain is not running: %s" % stderr
                        logging.warning(msg)
                        with open(ops_log, "a") as f:
                            f.write(msg + "\n")
                        break
                    else:
                        msg = "  [virsh dump] ERROR: %s" % stderr
                        logging.error(msg)
                        with open(ops_log, "a") as f:
                            f.write(msg + "\n")
                        failures.append("Iteration %d virsh dump failed: %s" % (i + 1, stderr))
                        break   
            except Exception as exc:
                msg = "  [virsh dump] EXCEPTION: %s" % exc
                logging.error(msg)
                with open(ops_log, "a") as f:
                    f.write(msg + "\n")
                failures.append("Iteration %d dump exception: %s" % (i + 1, exc))
                break

            if not dump_ok:
                break

            completed += 1
            with open(ops_log, "a") as f:
                f.write("  Iteration %d: COMPLETE\n" % (i + 1))

        with open(ops_log, "a") as f:
            f.write("\n" + "=" * 80 + "\n")
            f.write("Completed %d/%d iterations at %s\n"
                    % (completed, iterations, time.strftime("%Y-%m-%d %H:%M:%S")))

        return completed
    
    try:
        if mem_model == "dimm":
            _was_running = vm.is_alive()
            if _was_running:
                vm.destroy(gracefully=True)

            _vmxml = VMXML.new_from_inactive_dumpxml(vm_name)

            _numa_present = False
            try:
                _cpuxml = _vmxml.cpu
                if _cpuxml is not None and _cpuxml.numa_cell:
                    _numa_present = True
            except Exception:
                pass

            if not _numa_present:
                logging.info("No NUMA nodes found on VM '%s' – configuring "
                             "prerequisites for DIMM hotplug", vm_name)

                _mem_kib = int(params.get("mem", "2097152"))
                _max_mem_kib = int(params.get("max_mem", "20971520"))
                _slots = int(params.get("slots", "40"))
                _mem_unit = params.get("mem_unit", "KiB")
                _smp = int(params.get("smp", "2"))
                _numa_cells = int(params.get("numa_cells", "1"))

                _vmxml.max_mem_rt = _max_mem_kib
                _vmxml.max_mem_rt_slots = _slots
                _vmxml.max_mem_rt_unit = _mem_unit
                _vmxml.max_mem = _mem_kib
                _vmxml.current_mem = _mem_kib

                _cpuxml = _vmxml.cpu
                if _cpuxml is None:
                    _cpuxml = VMCPUXML()
                _cell_mem = _mem_kib // _numa_cells
                _cpu_range = "0-%d" % (_smp - 1) if _smp > 1 else "0"
                _cell_dicts = [
                    {"id": str(i), "cpus": _cpu_range,
                     "memory": str(_cell_mem), "unit": _mem_unit}
                    for i in range(_numa_cells)
                ]
                _cpuxml.numa_cell = _cpuxml.dicts_to_cells(_cell_dicts)
                _vmxml.cpu = _cpuxml

                _vmxml.sync()
                logging.info("NUMA configured for VM '%s': %d cell(s), "
                             "mem=%d %s, maxMem=%d %s, slots=%d",
                             vm_name, _numa_cells, _mem_kib, _mem_unit,
                             _max_mem_kib, _mem_unit, _slots)
            else:
                logging.debug("NUMA already configured for VM '%s' – skipping",
                              vm_name)

            if _was_running:
                vm.start()

        if not vm.is_alive():
            vm.start()

        vm.wait_for_login(timeout=240)
        logging.info("VM is running and accessible")

        logging.info("Starting %d sequential iterations "
                     "(attach-device then virsh dump per cycle)", mem_hotplug_iterations)
        completed = run_iterations(
            vm_name,
            mem_hotplug_iterations,
            virshdump_delay,
            dump_path
        )

        if os.path.exists(ops_log):
            with open(ops_log, "r") as f:
                content = f.read()
            error_count = content.count("ERROR")
            attach_ok = content.count("attach-device slot") - error_count
            dump_ok = content.count("virsh dump") - error_count
            logging.info("attach-device successes: %d | virsh dump successes: %d "
                         "| completed iterations: %d/%d",
                         attach_ok, dump_ok, completed, mem_hotplug_iterations)
        else:
            failures.append("Operations log not found: %s" % ops_log)

        if completed < mem_hotplug_iterations:
            failures.append(
                "Only %d/%d iterations completed successfully – check %s"
                % (completed, mem_hotplug_iterations, ops_log)
            )

        if failures:
            test.fail("Test failed with errors:\n" + "\n".join(failures))
        else:
            logging.info("SUCCESS: All %d iterations completed "
                         "(attach-device + virsh dump each)", completed)

    except Exception as e:
        test.error("Test execution failed: %s" % str(e))

    finally:
        logging.info("Cleaning up...")
