# -*- coding: utf-8 -*-
# Copyright (c) 2014, Vispy Development Team.
# Distributed under the (new) BSD License. See LICENSE.txt for more info.

from __future__ import print_function, division, absolute_import

import numpy as np

from .. import gloo
from ..gloo import gl
from . import BaseVisual
from ..shaders.composite import Function, FunctionTemplate, CompositeProgram, FragmentFunction
from .transforms import NullTransform



vertex_shader = """
// local_position function must return the current vertex position
// in the Visual's local coordinate system.
vec4 local_position(void);

// mapping function that transforms from the Visual's local coordinate
// system to normalized device coordinates.
vec4 map_local_to_nd(vec4);

// generic hook for executing code after the vertex position has been set
void post_hook();

void main(void) {
    vec4 local_pos = local_position();
    vec4 nd_pos = map_local_to_nd(local_pos);
    gl_Position = nd_pos;
    
    post_hook();
}
"""

fragment_shader = """
// Must return the color for this fragment
// or discard.
vec4 frag_color();

void main(void) {
    gl_FragColor = frag_color();
}
"""    
      

# generate local coordinate from xy (vec2) attribute and z (float) uniform
# Note that the Function and FunctionTemplate approaches
# should work equally well.
#XYInputFunc = Function("""
#vec4 v2_f_to_v4(vec2 xy_pos, float z_pos) {
    #return vec4(xy_pos, z_pos, 1.0);
#}
#""")
XYInputFunc = FunctionTemplate("""
vec4 $func_name() {
    return vec4($xy_pos, $z_pos, 1.0);
}
""", var_names=['xy_pos', 'z_pos'])


# generate local coordinate from xyz (vec3) attribute
#XYZInputFunc = Function("""
#vec4 v3_to_v4(vec3 xyz_pos) {
    #return vec4(xyz_pos, 1.0);
#}
#""")
XYZInputFunc = FunctionTemplate("""
vec4 $func_name() {
    return vec4($xyz_pos, 1.0);
}
""", var_names=['xyz_pos'])

# pair of functions used to provide uniform/attribute input to fragment shader
#RGBAInputFunc = FunctionTemplate("""
#vec4 $func_name() {
    #return $rgba;
#}
#""", var_names=['rgba'])
#RGBAVertexInputFunc = FunctionTemplate("""
#void $func_name() {
    #$output = $input;
#}
#""", var_names=['input', 'output'])

RGBAAttributeFunc = FragmentFunction(
    frag_func=FunctionTemplate("""
        vec4 $func_name() {
            return $rgba;
        }
        """, 
        var_names=['rgba']),
    vertex_post=FunctionTemplate("""
        void $func_name() {
            $output = $input;
        }
        """, 
        var_names=['input', 'output']),
    # vertex variable 'output' and fragment variable 'rgba' should both 
    # be bound to the same vec4 varying.
    link_vars=[('vec4', 'output', 'rgba')]
    )

RGBAUniformFunc = FunctionTemplate("""
vec4 $func_name() {
    return $rgba;
}
""", var_names=['rgba'])
    
class LineVisual(BaseVisual):
    def __init__(self, pos=None, color=None, width=None):
        #super(LineVisual, self).__init__()
        
        self._opts = {
            'pos': None,
            'color': (1, 1, 1, 1),
            'width': 1,
            'transform': NullTransform(),
            }
        
        self._program = None
        self._vbo = None
        
        self.set_data(pos=pos, color=color, width=width)

    @property
    def transform(self):
        return self._opts['transform']
    
    @transform.setter
    def transform(self, tr):
        self._opts['transform'] = tr
        self._program = None

    def set_data(self, pos=None, color=None, width=None):
        """
        Keyword arguments:
        pos     (N, 2-3) array
        color   (3-4) or (N, 3-4) array
        width   scalar or (N,) array
        """
        if pos is not None:
            self._opts['pos'] = pos
        if color is not None:
            self._opts['color'] = color
        if width is not None:
            self._opts['width'] = width
            
        # might need to rebuild vbo or program.. 
        # this could be made more clever.
        self._vbo = None
        self._program = None

    def _build_vbo(self):
        # Construct complete data array with position and optionally color
        
        pos = self._opts['pos']
        typ = [('pos', np.float32, pos.shape[-1])]
        color = self._opts['color']
        color_is_array = isinstance(color, np.ndarray) and color.ndim > 1
        if color_is_array:
            typ.append(('color', np.float32, self._opts['color'].shape[-1]))
        
        self._data = np.empty(pos.shape[:-1], typ)
        self._data['pos'] = pos
        if color_is_array:
            self._data['color'] = color
            
        # convert to vertex buffer
        self._vbo = gloo.VertexBuffer(self._data)
        
        
    def _build_program(self):
        if self._vbo is None:
            self._build_vbo()
        
        # Create composite program
        self._program = CompositeProgram(vmain=vertex_shader, fmain=fragment_shader)
        
        # Attach position input component
        pos_func = self._get_position_function()
        self._program.set_hook('local_position', pos_func)
        
        # Attach transformation function
        tr_bound = self.transform.bind_map('map_local_to_nd')
        self._program.set_hook('map_local_to_nd', tr_bound)
        
        # Attach color input function
        color_func = self._get_color_func()
        self._program.set_hook('frag_color', color_func)
        
    def paint(self):
        if self._opts['pos'] is None or len(self._opts['pos']) == 0:
            return
        
        if self._program is None:
            self._build_program()
            
        gl.glLineWidth(self._opts['width'])
        self._program.draw('LINE_STRIP')



    def _get_position_function(self):
        # select the correct shader function to read in vertex data based on 
        # position array shape
        if self._data['pos'].shape[-1] == 2:
            func = XYInputFunc.bind(
                        name='local_position', 
                        xy_pos=('attribute', 'vec2', 'input_xy_pos'),
                        z_pos=('uniform', 'float', 'input_z_pos'))
            func['input_xy_pos'] = self._vbo['pos']
            func['input_z_pos'] = 0.0
        else:
            func = XYZInputFunc.bind(
                        name='local_position', 
                        xyz_pos=('attribute', 'vec3', 'input_xyz_pos'))
            func['input_xyz_pos'] = self._vbo
        return func

    def _get_color_func(self):
        # Select uniform- or attribute-input 
        if 'color' in self._data.dtype.fields:
            func = RGBAAttributeFunc.bind(
                            name='frag_color',
                            input=('attribute', 'vec4', 'input_color')
                            )
            func['input_color'] = self._vbo['color']
        else:
            func = RGBAUniformFunc.bind('frag_color', 
                                             rgba=('uniform', 'vec4', 'input_color'))
            func['input_color'] = np.array(self._opts['color'])
        return func
