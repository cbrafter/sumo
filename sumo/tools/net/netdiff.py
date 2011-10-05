#!/usr/bin/env python
"""
@file    netdiff.py
@author  Jakob.Erdmann@dlr.de
@date    2011-10-04
@version $Id$

Reads two networks (source, dest) and tries to produce the minimal plain-xml input
which can be loaded with netconvert alongside source to create dest

Copyright (C) 2011 DLR (http://www.dlr.de/) and contributors
All rights reserved
"""

import sys
import os
import StringIO
from xml.dom import pulldom
from xml.dom import Node
from optparse import OptionParser
from subprocess import call
from collections import namedtuple, defaultdict
sys.path.append(os.path.join(os.path.dirname(sys.argv[0]), '..', 'lib'))
from testUtil import checkBinary

INDENT = 4
PLAIN_TYPES = [
        '.nod.xml', 
        '.edg.xml', 
        '.con.xml' # different logic for deletes, 
        #  cannot start to print creates until the whole of dest is read
        
        ] # file types to compare
ID_ATTR = 'id' # attribute for unique identifier
DELETE_ELEMENT = 'reset' # the xml element for signifying deletes

# provide an order for the attribute names
ATTRIBUTE_NAMES = {
        #'.nod.xml' : ()
        #'.edg.xml' : () 
        #'.con.xml' : ()
        }

# default values for the given attribute (needed when attributes appear in source but do not appear in dest)
DEFAULT_VALUES = defaultdict(lambda: "")
DEFAULT_VALUES['width'] = "-1"
RESET = 0


# stores attributes for later comparison
class AttributeStore:

    def __init__(self, level=1):
        # indent level
        self.level = level
        # dict of names-tuples
        self.attrnames = {}
        # sets of (tag, id)
        self.ids_deleted = set()
        self.ids_created = set()
        # dict from (tag, id) to (names, values)
        self.id_attrs = {}
        # dict from tag to (names, values)-sets
        self.idless_deleted = defaultdict(lambda:set())
        self.idless_created = defaultdict(lambda:set())


    # getAttribute returns "" if not present
    def getValue(self, node, name):
        if node.hasAttribute(name):
            return node.getAttribute(name)
        else:
            return None


    def getNames(self, xmlnode):
        a = xmlnode.attributes
        all = [a.item(i).localName for i in range(a.length)]
        instance = tuple([n for n in all if n != ID_ATTR])
        if not instance in self.attrnames:
            self.attrnames[instance] = instance
        # only store a single instance of this tuple to conserve memory
        return self.attrnames[instance]


    def getAttrs(self, xmlnode):
        names = self.getNames(xmlnode)
        values = tuple([self.getValue(xmlnode, a) for a in names])
        children = None
        if any([c.nodeType == Node.ELEMENT_NODE for c in xmlnode.childNodes]):
            children = AttributeStore(self.level + 1)
        tag = xmlnode.localName
        id = xmlnode.getAttribute(ID_ATTR)
        return tag, id, children, (names, values, children)


    def store(self, xmlnode):
        tag, id, children, attrs = self.getAttrs(xmlnode)
        tagid = (tag, id)
        if id != "":
            self.ids_deleted.add(tagid)
            self.id_attrs[tagid] = attrs
            if children:
                for child in xmlnode.childNodes:
                    if child.nodeType == Node.ELEMENT_NODE:
                        children.store(child)
        else:
            self.no_children_supported(children)
            self.idless_deleted[tag].add(attrs)


    def compare(self, xmlnode):
        tag, id, children, attrs = self.getAttrs(xmlnode)
        tagid = (tag, id)
        if id != "":
            if tagid in self.ids_deleted:
                self.ids_deleted.remove(tagid)
                self.id_attrs[tagid] = self.compareAttrs(self.id_attrs[tagid], attrs)
            else:
                self.ids_created.add(tagid)
                self.id_attrs[tagid] = attrs

            children = self.id_attrs[tagid][2]
            if children:
                for child in xmlnode.childNodes:
                    if child.nodeType == Node.ELEMENT_NODE:
                        children.compare(child)
        else:
            self.no_children_supported(children)
            if attrs in self.idless_deleted[tag]:
                self.idless_deleted[tag].remove(attrs)
            else:
                self.idless_created[tag].add(attrs)


    def no_children_supported(self, children):
        if children:
            print("WARNING: Handling of children only supported for elements with id. Ignored for element '%s'" % tag)


    def compareAttrs(self, sourceAttrs, destAttrs):
        snames, svalues, schildren = sourceAttrs
        dnames, dvalues, dchildren = destAttrs
        if schildren and dchildren:
            dchildren = schildren
        if snames == dnames:
            values = tuple([self.diff(n,s,d) for n,s,d in zip (snames,svalues,dvalues)])
            return snames, values, dchildren
        else:
            sdict = defaultdict(lambda:None, zip(snames, svalues))
            ddict = defaultdict(lambda:None, zip(dnames, dvalues))
            names = tuple(set(snames + dnames))
            values = tuple([self.diff(n,sdict[n],ddict[n]) for n in names])
            return names, values, dchildren


    def diff(self, name, sourceValue, destValue):
        if sourceValue == destValue:
            return None
        elif destValue == None:
            return DEFAULT_VALUES[name]
        else:
            return destValue


    def writeDeleted(self, file):
        # data loss if two elements with different tags 
        # have the same id
        for tag, id in self.ids_deleted:
            self.write(file, '<%s %s="%s"/>\n' % (DELETE_ELEMENT, ID_ATTR, id))
        # data loss if two elements with different tags 
        # have the same list of attributes and values
        for value_set in self.idless_deleted.itervalues():
            self.write_idless(file, value_set, DELETE_ELEMENT)


    def writeCreated(self, file):
        self.write_tagids(file, self.ids_created)
        for tag, value_set in self.idless_created.iteritems():
            self.write_idless(file, value_set, tag)


    def writeChanged(self, file):
        tagids_changed = set(self.id_attrs.keys()) - (self.ids_deleted | self.ids_created)
        self.write_tagids(file, tagids_changed)


    def write_idless(self, file, attr_set, tag):
        for names, values, children in attr_set:
            self.write(file, '<%s %s/>\n' % (tag, self.attr_string(names, values)))


    def write_tagids(self, file, tagids):
        for tagid in tagids:
            tag, id = tagid
            names, values, children = self.id_attrs[tagid]
            attrs = self.attr_string(names, values)
            child_strings = StringIO.StringIO()
            if children:
                # writeDeleted is not supported
                children.writeCreated(child_strings)
                children.writeChanged(child_strings)

            if len(attrs) > 0 or child_strings.len > 0:
                close_tag = "/>\n"
                if child_strings.len > 0:
                    close_tag = ">\n%s" % child_strings.getvalue()
                self.write(file, '<%s %s="%s" %s%s' % (
                    tag,
                    ID_ATTR, id,
                    attrs,
                    close_tag))
                if child_strings.len > 0:
                    self.write(file, "</%s>\n" % tag)


    def write(self, file, item):
        file.write(" " * INDENT * self.level)
        file.write(item)


    def attr_string(self, names, values):
        return ' '.join(['%s="%s"' % (n,v) for n,v in zip(names, values) if v != None])


def parse_args():
    USAGE = "Usage: " + sys.argv[0] + " <source> <dest> <output-prefix>"
    optParser = OptionParser()
    optParser.add_option("-v", "--verbose", action="store_true",
            default=False, help="Give more output")
    optParser.add_option("-p", "--use-prefix", action="store_true",
            default=False, help="interpret source and dest as plain-xml prefix instead of network names")
    optParser.add_option("--path", dest="path",
            default=os.environ.get("SUMO_BINDIR", ""), help="Path to binaries")
    options, args = optParser.parse_args()
    if len(args) != 3:
        sys.exit(USAGE)
    options.source, options.dest, options.outprefix = args
    return options 


def create_plain(netfile):
    netconvert = checkBinary("netconvert", options.path)        
    prefix = netfile[:-8]
    call([netconvert, 
        "--sumo-net-file", netfile, 
        "--plain-output-prefix", prefix])
    return prefix


# creates diff of a flat xml structure 
# (only children of the root element and their attrs are compared)
def xmldiff(source, dest, diff, type):
    attributeStore = AttributeStore()
    root_open, root_close = handle_children(source, attributeStore.store)
    handle_children(dest, attributeStore.compare)

    with open(diff, 'w') as diff_file:
        diff_file.write(root_open)
        attributeStore.write(diff_file, "<!-- Deleted Elements -->\n")
        attributeStore.writeDeleted(diff_file)
        attributeStore.write(diff_file, "<!-- Created Elements -->\n")
        attributeStore.writeCreated(diff_file)
        attributeStore.write(diff_file, "<!-- Changed Elements -->\n")
        attributeStore.writeChanged(diff_file)
        diff_file.write(root_close)


# calls function handle_parsenode for all children of the root element
# returns opening and closing tag of the root element
def handle_children(xmlfile, handle_parsenode):
    root_open = None
    root_close = None
    xml_doc = pulldom.parse(xmlfile)
    level = 0
    for event, parsenode in xml_doc:
        if event == pulldom.START_ELEMENT: 
            # print level, parsenode.getAttribute(ID_ATTR)
            if level == 0:
                root_open = parsenode.toprettyxml(indent="")
                # since we did not expand root_open contains the closing slash
                root_open = root_open[:-3] + ">\n"
                root_close = "</%s>\n" % parsenode.localName
            if level == 1:
                xml_doc.expandNode(parsenode) # consumes END_ELEMENT, no level increase
                handle_parsenode(parsenode)
            else:
                level += 1
        elif event == pulldom.END_ELEMENT: 
            level -= 1
    return root_open, root_close


# run
options = parse_args()
if not options.use_prefix:
    options.source = create_plain(options.source)
    options.dest = create_plain(options.dest)
for type in PLAIN_TYPES:
    xmldiff(options.source + type, 
            options.dest + type, 
            options.outprefix + type, 
            type)

