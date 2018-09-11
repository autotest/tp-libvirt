import logging


def set_nwfilter_iface(new_iface,
                       type_name="network",
                       source={'network': "default"},
                       filterref_dict={}):
    """
    set iface to bind network or bind filter

    Params new_iface: instance of Interface,
    Params type_name: the type name which bind to interface
    Params source: source of new_iface, set as default
    Params filterref_dict: dict of network filter, which is not
    blank will bind to new_iface

    return:  new network interface
    """
    # set iface type name as network and binding source type
    new_iface.type_name = type_name
    new_iface.source = source
    # if filterref_dict is not blank , bind it to iface
    if filterref_dict:
        filterref = new_iface.new_filterref(**filterref_dict)
        new_iface.filterref = filterref
    # print the new iface xml
    logging.debug("new iface xml is: \n %s \n" % new_iface)
    return new_iface
