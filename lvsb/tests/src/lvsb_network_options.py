import logging

from virttest.lvsb import make_sandboxes


def verify_network(params):
    """
    Verify network
    """
    # Results set list
    ret = []
    net_opt1 = None
    net_opt2 = None
    net_arg1 = None
    net_arg2 = None
    opts_count = int(params.get("lvsb_opts_count", 1))
    logging.debug("The network option numbers: %d", opts_count)

    cmd_outputs = params['lvsb_result']
    logging.debug("The network information from command output:\n%s",
                  cmd_outputs)

    # Simply check network interface numbers for multi-network
    if cmd_outputs[0] == str(opts_count):
        return True

    for i in range(1, opts_count + 1):
        options = params.get("lvsb_network_options%d" % i, 1)
        # e.g dhcp,source=default
        if "=" in options:
            # To get real network option, it may be 'dhcp', 'source',
            # 'mac', 'address', 'route' or a combination of them
            net_opts = options.split("=")[0].split(",")
            if net_opts:
                net_opt1 = net_opts[0]
                logging.debug("The network option 1: %s", net_opt1)
            if len(net_opts) == 2:
                net_opt2 = net_opts[1]
                logging.debug("The network option 2: %s", net_opt2)
            # To get network arguments
            # e.g address=192.168.122.100/24,route=0.0.0.0/24%192.168.122.1
            net_args = options.split("=")
            if len(net_args) >= 2:
                net_arg1 = net_args[1]
                if "," in net_arg1:
                    net_arg = net_arg1.split(",")
                    net_arg1 = net_arg[0]
                    if len(net_arg) > 1:
                        net_opt2 = net_arg[1]
                    net_arg2 = net_args[2]
                    logging.debug("The network argument 2: '%s'",
                                  net_arg2)
                logging.debug("The network argument 1: %s", net_arg1)

            # The command return value should be enough, don't need to
            # check dhcp with source and static network again.
            if net_opt1 == "dhcp" and net_opt2 == "source" or \
                    net_opt1 == "address" and not net_opt2:
                return True

            # Check MAC address
            # e.g mac=00:11:22:33:44:55
            if net_opt1 == "mac":
                ret.append(net_arg1 in cmd_outputs[0])
            if net_opt2 == "mac":
                ret.append(net_arg2 in cmd_outputs[0])

            # Check source address and route
            # e.g address=192.168.122.100/24,route=0.0.0.0/24%192.168.122.1
            if net_opt1 == "address" and net_opt2 == "route":
                # The route looks like "0.0.0.0/24 via 192.168.122.1 dev eth0
                # 192.168.122.0/24 dev eth0  proto kernel  scope link  src
                # 192.168.122.100"
                src_addr = net_arg1.split('/')[0]
                route_info = net_arg2.replace("%", " via ")
                ret.append(src_addr in cmd_outputs[1])
                ret.append(route_info in cmd_outputs[0])
        else:
            return False

    if False in ret:
        return False
    else:
        return True


def run(test, params, env):
    """
    Test network options of virt-sandbox command

    1) Positive testing
       1.1) Configure the network interface using dhcp
       1.2) Configure the network interface with the static
            IPv4 or IPv6 address
       1.3) Set the MAC address of the network interface
       1.4) Configure the network interface with the static
            IPv4 or IPv6 route
       1.5) Set multiple network interfaces
    2) Negative testing
       2.1) invalid network option
       2.2) invalid virtual network
       2.3) invalid static IPv4 or IPv6 network
       2.4) invalid MAC address
       2.5) Multicast address as MAC address
       2.6) invalid route argument
       2.7) static route without addresses
       2.8) static route with DHCP
       2.9) static route without gateway
    """
    status_error = bool("yes" == params.get("status_error", "no"))
    timeout = params.get("lvsb_network_timeout", 5)

    # list of sandbox agregation managers
    sb_list = make_sandboxes(params, env)
    if not sb_list:
        test.fail("Failed to return list of instantiated "
                  "lvsb_testsandboxes classes")

    # Run a sandbox until timeout or finished w/ output
    # store list of stdout's for the sandbox in aggregate type
    cmd_output_list = sb_list[0].results(int(timeout))
    # Remove all duplicate items from result list
    cmd_outputs = list(set(cmd_output_list[0].splitlines()))

    # To get exit codes of the command
    status = sb_list[0].are_failed()

    _params = dict(params)
    # To get output of the command
    _params['lvsb_result'] = cmd_outputs

    # positive and negative testing #########
    if status_error:
        if status == 0:
            test.fail("%d not a expected command "
                      "return value" % status)
        else:
            logging.info("It's an expected error: %s", _params['lvsb_result'])
    else:
        if status != 0:
            test.fail("%d not a expected command "
                      "return value" % status)
        if not verify_network(_params):
            test.fail("The network doesn't match!!")
