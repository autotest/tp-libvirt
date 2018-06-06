#!/usr/bin/env python

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
# See LICENSE for more details.
#
#
# Copyright: 2017 IBM
# Author: Prudhvi Miryala<mprudhvi@linux.vnet.ibm.com>

"""
This scripts basic EEH tests on all PCI device
"""
import logging
from virttest import virsh
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml import xcepts
from virttest.libvirt_xml.devices.controller import Controller

# on guest we can create maximum 32 vphbs
MAX_PCI = 32


def run(test, params, env):
    """
    1) get params from cfg file
    2) create a controller with index equal to 1
    3) add controller to xml
    4) start the Vm
       Vm should start with index 1 to 31, for index 32 it should not.
    5) shutdown the Vm
    6) repeat the steps  2 to 5, but each time
       increase the index value in step2
    """
    # get the params from params
    vm_name = params.get("main_vm")
    model = params.get("model", "ENTER_MODEL")
    if model.count("ENTER_MODEL"):
        test.cancel("Please enter model.")
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    devices = vmxml.get_devices()
    flag = True
    sec_falg = True
    try:
        for index in range(1, MAX_PCI+1):
            controller = Controller("controller")
            controller.type = "pci"
            controller.index = index
            controller.model = model
            devices.append(controller)
            try:
                vmxml.set_devices(devices)
                vmxml.sync()
            except xcepts.LibvirtXMLError, detail:
                if index < MAX_PCI:
                    logging.debug(detail)
                    flag = False
                else:
                    logging.debug("PPC supports maximum 32 vphbs(0 to 31) %s")
                continue
            ret = virsh.start(vm_name, ignore_status=True)
            if not ret.exit_status:
                logging.debug("Vm start with controller index %s" % index)
            elif ret.exit_status:
                logging.debug(ret)
                sec_falg = False
            virsh.shutdown(vm_name)
        if not flag:
            test.fail("Vm fail to define with newly added controller index")
        if not sec_falg:
            test.fail("Vm failed to start with newly added controller")
    finally:
        backup_xml.sync()
