#!/usr/bin/python

# Copyright (c) 2015 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from re import search
import tarfile
import subprocess
import sys
import yaml

# Uses the faster C implementation if available
try:
    import xml.etree.cElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET


# Removes the namespace in front of tags
def _rns(tag):
    match = search(r'\{.*?\}(.*)', tag)
    return match.group(1) if match else tag


# Maximum properties allowed by Glance
MAX_PROPS = 128
cpu, memory = None, None


"""
Uses SAX-like on-the-fly parsing of the xml file
:param ovf: Passed as a filename or file object

:retval: Returns OVF properties as key-value mapping
"""
def _parse_ovf_sax(ovf):
    ovf_prop = {}
    paths = []
    path = ''
    global cpu
    global memory
    cpu, memory = None, None
    for event, elem in ET.iterparse(ovf, events=('start', 'end')):
        if _rns(elem.tag) == 'Envelope': continue
        if event == 'start':
            path += _rns(elem.tag) if path == '' else '.%s' % _rns(elem.tag)
            count = 1
            while path in paths:
                count += 1
                path = path + str(count) if count == 2 else path[:-1] + str(count)
            paths.append(path) 
 
        if event == 'end':
            for attr in elem.attrib:
                if len(ovf_prop) < MAX_PROPS:
                    ovf_prop['%s.%s' % (path, _rns(attr))] = \
                        elem.attrib[attr]
            content = elem.text.strip() if elem.text else ''
            if content and len(ovf_prop) < MAX_PROPS: ovf_prop[path] = content
            if _rns(elem.tag) == 'Item':
                children = elem.getchildren()
                properties = {_rns(child.tag): (child.text.strip() if child.text else None)
                                 for child in children}
                resource_type = properties.get('ResourceType')
                # get virtual cpu numbers
                if resource_type == '3':
                    cpu = int(properties.get('VirtualQuantity'))
                # get memory (assume in MB)
                elif resource_type == '4':
                    memory = int(properties.get('VirtualQuantity'))
 
            while path[-1].isdigit():
                path = path[:-1]
            path = path[:len(path) - len(_rns(elem.tag))]
            if path != '': path = path[:-1]
            #elem.clear()

    return ovf_prop


def write_heat_template(image_id):
    print 'Writing Heat Template for %s CPU and %s MB memory...' % (cpu, memory)
    # Default flavor config
    flavor = 'm1.small'
    
    if memory:
        if memory <= 512:
            flavor = 'm1.tiny'
        elif memory <= 2048:
            flavor = 'm1.small'
        elif memory <= 4096:
            flavor = 'm1.medium'
        elif memory <= 8192:
            flavor = 'm1.large'
        elif memory <= 16384:
            flavor = 'm1.xlarge'
        else:
            flavor = 'm1.xlarge'

    if cpu:
        if cpu <= 1:
            pass
        elif cpu <= 2:
            flavor = 'm1.medium'
        elif cpu <= 4:
            flavor = 'm1.large'
        elif cpu <= 8:
            flavor = 'm1.xlarge'
        else:
            flavor = 'm1.xlarge'
 
    template = {
                'heat_template_version': '2015-04-30',
                'description': 'Heat template for OVA image',
                'resources': {
                    'instance': {
                        'type': 'OS::Nova::Server',
                        'properties': {
                            'image': image_id,
                            'flavor': flavor
                        }
                    }
                }
               }

    with open('template.yaml', 'w') as f:
        output = yaml.dump(template, default_flow_style=False)
        print 
        print output
        f.write(output)
        #print ordered_dump(template, Dumper=yaml.SafeDumper, default_flow_style=False)


"""
:param ovf: Passed as a filename or file object

:retval: Returns OVF properties as key-value mapping
"""
def parse_OVF(ovf):
    #print 'Parsing OVF file...'
    properties = _parse_ovf_sax(ovf)

    return properties


"""
Uploads Glance Image with OVF properties from OVA file
"""
def create_glance_image_from_OVA(ova_filename, glance_image_name):
    print 'Unpacking OVA file...'
    with tarfile.open(name=ova_filename) as tar_file:
        filenames = tar_file.getnames()
        ovf_filename = None
        disk_name = None
        disk_format = None
        properties = None
        valid_disk_formats = ['.aki', '.ari', '.ami', '.raw', '.iso', '.vhd',
                              '.vdi', '.qcow2', '.vmdk']
        for filename in filenames:
            if filename[-4:] == '.ovf':
                ovf_filename = filename
                ovf_file = tar_file.extractfile(filename)
                properties = parse_OVF(ovf_file)
                ovf_file.close()
                continue
            for format in valid_disk_formats:
                if format in filename:
                    disk_name = filename
                    disk_format = format[1:]

        if not disk_name:
            raise Exception('Disk Image expected in OVA package') 
        if not ovf_filename:
            raise Exception('OVF file expected in OVA package')
        
        # Extract to disk
        tar_file.extractall()

        headers = ['%s=%s' % (key, value) for key, value in properties.items()]
        command = ['glance', 'image-create', '--name', glance_image_name,
                   '--disk-format', disk_format, '--container-format', 'bare',
                   '--file', disk_name]
        for header in headers:
            command.extend(['--property', header])
        print 'Uploading image to Glance...'
        subprocess.call(command)

        # Clean up
        for file in [disk_name, ovf_filename]:
            subprocess.call(['rm', file])


def main():
    try:
        file = sys.argv[1]
    except IndexError:
        print 'Usage error: Specify the OVA file to be imported and optionally a name for the image'
        return
    try:
        name = sys.argv[2]
    except IndexError:
        name = 'demo_image'
    create_glance_image_from_OVA(file, name)
    write_heat_template(name)


if __name__ == '__main__':
    main()
