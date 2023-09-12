import re

from avocado.core import exceptions


def convert_data_size(current_size, dest_unit="KiB"):
    """
    Convert source value to expected value

    :param current_size: current size str, eg: 1024MB
    :param dest_unit: dest size unit, eg: KiB, MiB
    :return: dest_size: The size is converted, eg: 1(the dest unit is given,
    so it means 1GB)
    """
    decimal = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB']
    binary = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB', 'EiB', 'ZiB', 'YiB']
    current_value = re.findall(r"\d+", current_size)[0]
    current_unit = re.findall(r"\D+", current_size)[0]

    if current_unit == "bytes":
        bytes_size = int(current_value)
    else:
        factor, unit_list = 0, []
        if current_unit in decimal:
            factor = 1000
            unit_list = decimal
        elif current_unit in binary:
            factor = 1024
            unit_list = binary
        else:
            raise exceptions.TestError("The unit:%s you input is not "
                                       "found" % current_unit)
        bytes_size = int(current_value) * (
                    factor ** (unit_list.index(current_unit)))

    if dest_unit in decimal:
        factor = 1000
        unit_list = decimal
    elif dest_unit in binary:
        factor = 1024
        unit_list = binary
    else:
        raise exceptions.TestError(
            "The unit:%s you input is not found" % current_unit)

    dest_size = bytes_size / (factor ** (unit_list.index(dest_unit)))
    if isinstance(dest_size, float):
        return dest_size
    return int(dest_size)
