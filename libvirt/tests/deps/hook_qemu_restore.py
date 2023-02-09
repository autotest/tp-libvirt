#! /usr/bin/python3

"""
This file works as a hook script which will be executed when vm is restored. This file
needs to be copied and rename to /etc/libvirt/hooks/qemu before the hook is activated.
"""

import sys


def input_xml():
    xml = ""
    lines = sys.stdin.readlines()
    xml = "".join(lines)
    xml = xml.replace("<on_crash>restart</on_crash>\n", "<on_crash>destroy</on_crash>\n")
    sys.stdout.write(xml)


def main():
    if sys.argv[1] == '%s' and sys.argv[2] == 'restore':
        input_xml()


if __name__ == '__main__':
    main()
