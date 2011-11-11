#!/usr/bin/env python
"""
@file    netdiff.py
@author  Jakob Erdmann
@date    2011-10-04
@version $Id$

Reads two networks (source, dest) and tries to produce the minimal plain-xml input
which can be loaded with netconvert alongside source to create dest

SUMO, Simulation of Urban MObility; see http://sumo.sourceforge.net/
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

# file types to compare
TYPE_NODES       = '.nod.xml'
TYPE_EDGES       = '.edg.xml'
TYPE_CONNECTIONS = '.con.xml'
TYPE_TLLOGICS    = '.tll.xml'
PLAIN_TYPES = [
        TYPE_NODES,
        TYPE_EDGES,
        TYPE_CONNECTIONS,
        TYPE_TLLOGICS
        ] 

# traffic lights have some peculiarities
# CAVEAT1 - ids are not unique (only in combination with programID)
# CAVEAT2 - the order of their children (phases) is important.
#     this makes partial diffs unfeasible. The easiest solution is to forgo diffs and always export the whole new traffic light
# CAVEAT3 - deletes need not be written because they are also signaled by a changed node type 
#     (and they complicate the handling of deleted tl-connections)
# CAVEAT4 - deleted connections must be written with their tlID and tlIndex, otherwise
#     parsing in netconvert becomes tedious
# CAVEAT5 - phases must maintain their order

TAG_TLL = 'tlLogic'
TAG_CONNECTION = 'connection'

# see CAVEAT1
def get_id_attrs(tag): 
    if tag == TAG_TLL:
        return ('id', 'programID')
    elif tag == TAG_CONNECTION:
        return ('from', 'to', 'fromLane', 'toLane')
    else:
        return ('id',)

DELETE_ELEMENT = 'delete' # the xml element for signifying deletes

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

    def __init__(self, type, level=1):
        # xml type being parsed
        self.type = type
        # indent level
        self.level = level
        # dict of names-tuples
        self.attrnames = {}
        # sets of (tag, id) preserve order to avoid dangling references during loading
        self.ids_deleted = OrderedSet()
        self.ids_created = OrderedSet()
        # dict from (tag, id) to (names, values)
        self.id_attrs = {}
        # dict from tag to (names, values)-sets, need to preserve order (CAVEAT5)
        self.idless_deleted = defaultdict(lambda:OrderedSet())
        self.idless_created = defaultdict(lambda:OrderedSet())


    # getAttribute returns "" if not present
    def getValue(self, node, name):
        if node.hasAttribute(name):
            return node.getAttribute(name)
        else:
            return None


    def getNames(self, xmlnode):
        idattrs = get_id_attrs(xmlnode.localName)
        a = xmlnode.attributes
        all = [a.item(i).localName for i in range(a.length)]
        instance = tuple([n for n in all if n not in idattrs])
        if not instance in self.attrnames:
            self.attrnames[instance] = instance
        # only store a single instance of this tuple to conserve memory
        return self.attrnames[instance]


    def getAttrs(self, xmlnode):
        names = self.getNames(xmlnode)
        values = tuple([self.getValue(xmlnode, a) for a in names])
        children = None
        if any([c.nodeType == Node.ELEMENT_NODE for c in xmlnode.childNodes]):
            children = AttributeStore(self.type, self.level + 1)
        tag = xmlnode.localName
        id = tuple([xmlnode.getAttribute(a) for a in get_id_attrs(tag) if xmlnode.hasAttribute(a)])
        return tag, id, children, (names, values, children)


    def store(self, xmlnode):
        tag, id, children, attrs = self.getAttrs(xmlnode)
        tagid = (tag, id)
        if id != ():
            self.ids_deleted.add(tagid)
            self.id_attrs[tagid] = attrs
            if children:
                for child in xmlnode.childNodes:
                    if child.nodeType == Node.ELEMENT_NODE:
                        children.store(child)
        else:
            self.no_children_supported(children, tag)
            self.idless_deleted[tag].add(attrs)


    def compare(self, xmlnode):
        tag, id, children, attrs = self.getAttrs(xmlnode)
        tagid = (tag, id)
        if id != ():
            if tagid in self.ids_deleted:
                self.ids_deleted.remove(tagid)
                self.id_attrs[tagid] = self.compareAttrs(self.id_attrs[tagid], attrs, tag)
            else:
                self.ids_created.add(tagid)
                self.id_attrs[tagid] = attrs

            children = self.id_attrs[tagid][2]
            if children:
                for child in xmlnode.childNodes:
                    if child.nodeType == Node.ELEMENT_NODE:
                        children.compare(child)
                if tag == TAG_TLL: # see CAVEAT2
                    child_strings = StringIO.StringIO()
                    children.writeDeleted(child_strings)
                    children.writeCreated(child_strings)
                    children.writeChanged(child_strings)
                    if child_strings.len > 0:
                        # there are some changes. Go back and store everything
                        children = AttributeStore(self.type, self.level + 1)
                        for child in xmlnode.childNodes:
                            if child.nodeType == Node.ELEMENT_NODE:
                                children.compare(child)
                        self.id_attrs[tagid] = self.id_attrs[tagid][0:2] + (children,)


        else:
            self.no_children_supported(children, tag)
            if attrs in self.idless_deleted[tag]:
                self.idless_deleted[tag].remove(attrs)
            else:
                self.idless_created[tag].add(attrs)


    def no_children_supported(self, children, tag):
        if children:
            print("WARNING: Handling of children only supported for elements without id. Ignored for element '%s'" % tag)


    def compareAttrs(self, sourceAttrs, destAttrs, tag):
        snames, svalues, schildren = sourceAttrs
        dnames, dvalues, dchildren = destAttrs
        # for traffic lights, always use dchildren
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
            additional = ""
            if self.type == TYPE_TLLOGICS and tag == TAG_CONNECTION:
                # see CAVEAT4
                names, values, children = self.id_attrs[(tag, id)]
                additional = " " + self.attr_string(names, values)
            if tag != TAG_TLL: # see CAVEAT3
                self.write(file, '<%s %s%s/>\n' % (
                    DELETE_ELEMENT, self.id_string(tag, id), additional))
        # data loss if two elements with different tags 
        # have the same list of attributes and values
        for value_set in self.idless_deleted.itervalues():
            self.write_idless(file, value_set, DELETE_ELEMENT)


    def writeCreated(self, file):
        self.write_tagids(file, self.ids_created, True)
        for tag, value_set in self.idless_created.iteritems():
            self.write_idless(file, value_set, tag)


    def writeChanged(self, file):
        tagids_changed = OrderedSet(self.id_attrs.keys()) - (self.ids_deleted | self.ids_created)
        self.write_tagids(file, tagids_changed, False)


    def write_idless(self, file, attr_set, tag):
        for names, values, children in attr_set:
            self.write(file, '<%s %s/>\n' % (tag, self.attr_string(names, values)))


    def write_tagids(self, file, tagids, create):
        for tagid in tagids:
            tag, id = tagid
            names, values, children = self.id_attrs[tagid]
            attrs = self.attr_string(names, values)
            child_strings = StringIO.StringIO()
            if children:
                # writeDeleted is not supported
                children.writeCreated(child_strings)
                children.writeChanged(child_strings)

            if len(attrs) > 0 or child_strings.len > 0 or create:
                close_tag = "/>\n"
                if child_strings.len > 0:
                    close_tag = ">\n%s" % child_strings.getvalue()
                self.write(file, '<%s %s %s%s' % (
                    tag,
                    self.id_string(tag, id),
                    attrs,
                    close_tag))
                if child_strings.len > 0:
                    self.write(file, "</%s>\n" % tag)


    def write(self, file, item):
        file.write(" " * INDENT * self.level)
        file.write(item)


    def attr_string(self, names, values):
        return ' '.join(['%s="%s"' % (n,v) for n,v in zip(names, values) if v != None])

    def id_string(self, tag, id):
        idattrs = get_id_attrs(tag)
        return ' '.join(['%s="%s"' % (n,v) for n,v in zip(idattrs, id)])



#######################################################################################3
# [http://code.activestate.com/recipes/576694/]
# (c) Raymond Hettinger, MIT-License
# added operators by Jakob Erdmann

import collections
KEY, PREV, NEXT = range(3)
class OrderedSet(collections.MutableSet):

    def __init__(self, iterable=None):
        self.end = end = [] 
        end += [None, end, end]         # sentinel node for doubly linked list
        self.map = {}                   # key --> [key, prev, next]
        if iterable is not None:
            self |= iterable

    def __len__(self):
        return len(self.map)

    def __contains__(self, key):
        return key in self.map

    def add(self, key):
        if key not in self.map:
            end = self.end
            curr = end[PREV]
            curr[NEXT] = end[PREV] = self.map[key] = [key, curr, end]

    def discard(self, key):
        if key in self.map:        
            key, prev, next = self.map.pop(key)
            prev[NEXT] = next
            next[PREV] = prev

    def __iter__(self):
        end = self.end
        curr = end[NEXT]
        while curr is not end:
            yield curr[KEY]
            curr = curr[NEXT]

    def __reversed__(self):
        end = self.end
        curr = end[PREV]
        while curr is not end:
            yield curr[KEY]
            curr = curr[PREV]

    def pop(self, last=True):
        if not self:
            raise KeyError('set is empty')
        key = next(reversed(self)) if last else next(iter(self))
        self.discard(key)
        return key

    def __repr__(self):
        if not self:
            return '%s()' % (self.__class__.__name__,)
        return '%s(%r)' % (self.__class__.__name__, list(self))

    def __eq__(self, other):
        if isinstance(other, OrderedSet):
            return len(self) == len(other) and list(self) == list(other)
        return set(self) == set(other)

    def __del__(self):
        self.clear()                    # remove circular references


    def __sub__(self, other):
        result = OrderedSet()
        for x in self:
            result.add(x)
        for x in other:
            result.discard(x)
        return result


    def __or__(self, other):
        result = OrderedSet()
        for x in self:
            result.add(x)
        for x in other:
            result.add(x)
        return result
#######################################################################################3


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
    attributeStore = AttributeStore(type)
    root_open = None
    if os.path.isfile(source):
        root_open, root_close = handle_children(source, attributeStore.store)
    else:
        print "Source file %s is missing. Assuming all elements are created" % source
    if os.path.isfile(dest):
        root_open, root_close = handle_children(dest, attributeStore.compare)
    else:
        print "Dest file %s is missing. Assuming all elements are deleted" % dest

    if root_open:
        with open(diff, 'w') as diff_file:
            diff_file.write(root_open)
            attributeStore.write(diff_file, "<!-- Deleted Elements -->\n")
            attributeStore.writeDeleted(diff_file)
            attributeStore.write(diff_file, "<!-- Created Elements -->\n")
            attributeStore.writeCreated(diff_file)
            attributeStore.write(diff_file, "<!-- Changed Elements -->\n")
            attributeStore.writeChanged(diff_file)
            diff_file.write(root_close)
    else:
        print "Skipping %s due to lack of input files" % diff


# calls function handle_parsenode for all children of the root element
# returns opening and closing tag of the root element
def handle_children(xmlfile, handle_parsenode):
    root_open = None
    root_close = None
    level = 0
    xml_doc = pulldom.parse(xmlfile)
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

