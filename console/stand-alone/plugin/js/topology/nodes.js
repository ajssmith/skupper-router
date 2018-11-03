/*
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
*/

/* global d3 Promise */
export class Node {
  constructor(QDRService, id, name, nodeType, properties, routerId, x, y, nodeIndex, resultIndex, fixed, connectionContainer) {
    this.key = id;
    this.name = name;
    this.nodeType = nodeType;
    this.properties = properties;
    this.routerId = routerId;
    this.x = x;
    this.y = y;
    this.id = nodeIndex;
    this.resultIndex = resultIndex;
    this.fixed = !!+fixed;
    this.cls = '';
    this.container = connectionContainer;
    this.isConsole = QDRService.utilities.isConsole(this);
    this.isArtemis = QDRService.utilities.isArtemis(this);
  }
  title () {
    let x = '';
    if (this.normals && this.normals.length > 1)
      x = ' x ' + this.normals.length;
    if (this.isConsole)
      return 'Dispatch console' + x;
    else if (this.isArtemis)
      return 'Broker - Artemis' + x;
    else if (this.properties.product == 'qpid-cpp')
      return 'Broker - qpid-cpp' + x;
    else if (this.nodeType === 'edge')
      return 'Edge Router';
    else if (this.cdir === 'in')
      return 'Sender' + x;
    else if (this.cdir === 'out')
      return 'Receiver' + x;
    else if (this.cdir === 'both')
      return 'Sender/Receiver' + x;
    else if (this.nodeType === 'normal')
      return 'client' + x;
    else if (this.nodeType === 'on-demand')
      return 'broker';
    else if (this.properties.product) {
      return this.properties.product;
    }
    else {
      return '';
    }
  }
  toolTip (QDRService) {
    return new Promise( (function (resolve) {
      if (this.nodeType === 'normal' || this.nodeType === 'edge') {
        resolve(this.clientTooltip());
      } else
        this.routerTooltip(QDRService)
          .then( function (toolTip) {
            resolve(toolTip);
          });
    }.bind(this)));
  }

  clientTooltip () {
    let type = this.title();
    let title = `<table class="popupTable"><tr><td>Type</td><td>${type}</td></tr>`;
    if (!this.normals || this.normals.length < 2)
      title += `<tr><td>Host</td><td>${this.host}</td></tr>`;
    else {
      title += `<tr><td>Count</td><td>${this.normals.length}</td></tr>`;
    }
    title += '</table>';
    return title;
  }

  routerTooltip (QDRService) {
    return new Promise( (function (resolve) {
      QDRService.management.topology.ensureEntities(this.key, [
        {entity: 'listener', attrs: ['role', 'port', 'http']},
        {entity: 'router', attrs: ['name', 'version', 'hostName']}
      ], function () {
        // update all the router title text
        let nodes = QDRService.management.topology.nodeInfo();
        let node = nodes[this.key];
        let listeners = node['listener'];
        let router = node['router'];
        let r = QDRService.utilities.flatten(router.attributeNames, router.results[0]);
        let title = '<table class="popupTable">';
        title += ('<tr><td>Router</td><td>' + r.name + '</td></tr>');
        if (r.hostName)
          title += ('<tr><td>Host Name</td><td>' + r.hostHame + '</td></tr>');
        title += ('<tr><td>Version</td><td>' + r.version + '</td></tr>');
        let ports = [];
        for (let l=0; l<listeners.results.length; l++) {
          let listener = QDRService.utilities.flatten(listeners.attributeNames, listeners.results[l]);
          if (listener.role === 'normal') {
            ports.push(listener.port+'');
          }
        }
        if (ports.length > 0) {
          title += ('<tr><td>Ports</td><td>' + ports.join(', ') + '</td></tr>');
        }
        title += '</table>';
        resolve(title);
        return title;
      }.bind(this));
    }.bind(this)));
  }
  radius() {
    return nodeProperties[this.nodeType].radius;
  }
}
const nodeProperties = {
  // router types
  'inter-router': {radius: 28, linkDistance: [150, 70], charge: [-1800, -900]},
  '_edge':  {radius: 20, linkDistance: [110, 55], charge: [-1350, -900]},
  '_topo': {radius: 28, linkDistance: [150, 70], charge: [-1800, -900]},
  // generated nodes from connections. key is from connection.role
  'normal':       {radius: 15, linkDistance: [75, 40], charge: [-900, -900]},
  'on-demand':    {radius: 15, linkDistance: [75, 40], charge: [-900, -900]},
  'route-container': {radius: 15, linkDistance: [75, 40], charge: [-900, -900]},
  'edge':  {radius: 20, linkDistance: [110, 55], charge: [-1350, -900]}
};

export class Nodes {
  constructor(QDRService, logger) {
    this.nodes = [];
    this.QDRService = QDRService;
    this.logger = logger;
  }
  static radius(type) {
    if (nodeProperties[type].radius)
      return nodeProperties[type].radius;
    console.log(`Requested radius for unknown node type: ${type}`);
    return 15;
  }
  static maxRadius() {
    let max = 0;
    for (let key in nodeProperties) {
      max = Math.max(max, nodeProperties[key].radius);
    }
    return max;
  }
  // return all possible values of node radii
  static discrete() {
    let values = {};
    for (let key in nodeProperties) {
      values[nodeProperties[key].radius] = true;
    }
    return Object.keys(values);
  }
  // vary the following force graph attributes based on nodeCount
  static forceScale (nodeCount, minmax) {
    let count = Math.max(Math.min(nodeCount, 80), 6);
    let x = d3.scale.linear()
      .domain([6,80])
      .range(minmax);
    return x(count);
  }
  linkDistance (d, nodeCount) {
    let range = nodeProperties[d.target.nodeType].linkDistance;
    return Nodes.forceScale(nodeCount, range);
  }
  charge (d, nodeCount) {
    let charge = nodeProperties[d.nodeType].charge;
    return Nodes.forceScale(nodeCount, charge);
  }
  gravity (d, nodeCount) {
    return Nodes.forceScale(nodeCount, [0.0001, 0.1]);
  }

  getLength () {
    return this.nodes.length;
  }
  get (index) {
    if (index < this.getLength()) {
      return this.nodes[index];
    }
    this.logger.error(`Attempted to get node[${index}] but there were only ${this.getLength()} nodes`);
    return undefined;
  }
  setNodesFixed (name, b) {
    this.nodes.some(function (n) {
      if (n.name === name) {
        n.fixed = b;
        if (!b)
          n.lat = n.lon = null;
        return true;
      }
    });
  }
  nodeFor (name) {
    for (let i = 0; i < this.nodes.length; ++i) {
      if (this.nodes[i].name == name)
        return this.nodes[i];
    }
    return null;
  }
  nodeExists (connectionContainer) {
    return this.nodes.findIndex( function (node) {
      return node.container === connectionContainer;
    });
  }
  normalExists (connectionContainer) {
    let normalInfo = {};
    for (let i=0; i<this.nodes.length; ++i) {
      if (this.nodes[i].normals) {
        if (this.nodes[i].normals.some(function (normal, j) {
          if (normal.container === connectionContainer && i !== j) {
            normalInfo = {nodesIndex: i, normalsIndex: j};
            return true;
          }
          return false;
        }))
          break;
      }
    }
    return normalInfo;
  }
  savePositions (nodes) {
    if (!nodes)
      nodes = this.nodes;
    if (Object.prototype.toString.call(nodes) !== '[object Array]') {
      nodes = [nodes];
    }
    this.nodes.forEach( function (d) {
      localStorage[d.name] = JSON.stringify({
        x: Math.round(d.x),
        y: Math.round(d.y),
        fixed: (d.fixed & 1) ? 1 : 0,
      });
    });
  }
  // Convert node's x,y coordinates to longitude, lattitude
  saveLonLat (backgroundMap, nodes) {
    if (!backgroundMap)
      return;
    // didn't pass nodes, use all nodes
    if (!nodes)
      nodes = this.nodes;
    // passed a single node, wrap it in an array
    if (Object.prototype.toString.call(nodes) !== '[object Array]') {
      nodes = [nodes];
    }
    for (let i=0; i<nodes.length; i++) {
      let n = nodes[i];
      if (n.fixed) {
        let lonlat = backgroundMap.getLonLat(n.x, n.y);
        if (lonlat) {
          n.lon = lonlat[0];
          n.lat = lonlat[1];
        }
      } else {
        n.lon = n.lat = null;
      }
    }
  }
  // convert all nodes' longitude,lattitude to x,y coordinates
  setXY (backgroundMap) {
    if (!backgroundMap)
      return;
    for (let i=0; i<this.nodes.length; i++) {
      let n = this.nodes[i];
      if (n.lon && n.lat) {
        let xy = backgroundMap.getXY(n.lon, n.lat);
        if (xy) {
          n.x = n.px = xy[0];
          n.y = n.py = xy[1];
        }
      }
    }
  }

  find (connectionContainer, properties, name) {
    properties = properties || {};
    for (let i=0; i<this.nodes.length; ++i) {
      if (this.nodes[i].name === name || this.nodes[i].container === connectionContainer) {
        if (properties.product)
          this.nodes[i].properties = properties;
        return this.nodes[i];
      }
    }
    return undefined;
  }
  getOrCreateNode (id, name, nodeType, nodeInfo, nodeIndex, x, y, 
    connectionContainer, resultIndex, fixed, properties) {
    properties = properties || {};
    let gotNode = this.find(connectionContainer, properties, name);
    if (gotNode) {
      return gotNode;
    }
    let routerId = this.QDRService.utilities.nameFromId(id);
    return new Node(this.QDRService, id, name, nodeType, properties, routerId, x, y, 
      nodeIndex, resultIndex, fixed, connectionContainer);
  }
  add (obj) {
    this.nodes.push(obj);
    return obj;
  }
  addUsing (id, name, nodeType, nodeInfo, nodeIndex, x, y, 
    connectContainer, resultIndex, fixed, properties) {
    let obj = this.getOrCreateNode(id, name, nodeType, nodeInfo, nodeIndex, x, y, 
      connectContainer, resultIndex, fixed, properties);
    this.nodes.push(obj);
    return obj;
  }
  clearHighlighted () {
    for (let i = 0; i<this.nodes.length; ++i) {
      this.nodes[i].highlighted = false;
    }
  }
  initialize (nodeInfo, localStorage, width, height) {
    let nodeCount = Object.keys(nodeInfo).length;
    let yInit = 50;
    let animate = false;
    for (let id in nodeInfo) {
      let name = this.QDRService.utilities.nameFromId(id);
      // if we have any new nodes, animate the force graph to position them
      let position = localStorage[name] ? JSON.parse(localStorage[name]) : undefined;
      if (!position) {
        animate = true;
        position = {
          x: Math.round(width / 4 + ((width / 2) / nodeCount) * this.nodes.length),
          y: Math.round(height / 2 + Math.sin(this.nodes.length / (Math.PI*2.0)) * height / 4),
          fixed: false,
        };
      }
      if (position.y > height) {
        position.y = 200 - yInit;
        yInit *= -1;
      }
      let parts = id.split('/');
      this.addUsing(id, name, parts[1], nodeInfo, this.nodes.length, position.x, position.y, name, undefined, position.fixed, {});
    }
    return animate;
  }
}

