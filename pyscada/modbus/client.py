# -*- coding: utf-8 -*-
from pyscada import log
from pyscada.models import ClientWriteTask
from pyscada.models import Client
from pyscada.models import RecordedTime
from pyscada.models import RecordedDataFloat
from pyscada.models import RecordedDataInt
from pyscada.models import RecordedDataBoolean
from pyscada.models import RecordedDataCache
from pyscada.modbus.utils import encode_value
from pyscada.modbus.utils import get_bits_by_class
from pyscada.modbus.utils import decode_value
from pyscada.utils import RecordData

from django.conf import settings
from pymodbus.client.sync import ModbusTcpClient as ModbusClient
from math import isnan, isinf
from time import time

class InputRegisterBlock:
    def __init__(self):
        self.variable_address   = [] #
        self.variable_length    = [] # in bytes
        self.variable_class     = [] #
        self.variable_id        = [] #


    def insert_item(self,variable_id,variable_address,variable_class,variable_length):
        if not self.variable_address:
            self.variable_address.append(variable_address)
            self.variable_length.append(variable_length)
            self.variable_class.append(variable_class)
            self.variable_id.append(variable_id)
        elif max(self.variable_address) < variable_address:
            self.variable_address.append(variable_address)
            self.variable_length.append(variable_length)
            self.variable_class.append(variable_class)
            self.variable_id.append(variable_id)
        elif min(self.variable_address) > variable_address:
            self.variable_address.insert(0,variable_address)
            self.variable_length.insert(0,variable_length)
            self.variable_class.insert(0,variable_class)
            self.variable_id.insert(0,variable_id)
        else:
            i = self.find_gap(self.variable_address,variable_address)
            if (i is not None):
                self.variable_address.insert(i,variable_address)
                self.variable_length.insert(i,variable_length)
                self.variable_class.insert(i,variable_class)
                self.variable_id.insert(i,variable_id)


    def request_data(self,slave):
        quantity = sum(self.variable_length) # number of bits to read
        first_address = min(self.variable_address)
        
        result = slave.read_input_registers(first_address,quantity/16)
        if not hasattr(result, 'registers'):
            return None

        return self.decode_data(result)
        
    
    def decode_data(self,result):
        out = {}
        #var_count = 0
        for idx in range(len(self.variable_length)):
            tmp = []
            for i in range(self.variable_length[idx]/16):
                tmp.append(result.registers.pop(0))
            out[self.variable_id[idx]] = decode_value(tmp,self.variable_class[idx])
            if isnan(out[self.variable_id[idx]]) or isinf(out[self.variable_id[idx]]):
                    out[self.variable_id[idx]] = None
        return out
    
    def find_gap(self,L,value):
        for index in range(len(L)):
            if L[index] == value:
                return None
            if L[index] > value:
                return index

class HoldingRegisterBlock(InputRegisterBlock):
    def request_data(self,slave):
        quantity = sum(self.variable_length) # number of bits to read
        first_address = min(self.variable_address)
        
        result = slave.read_holding_registers(first_address,quantity/16)
        if not hasattr(result, 'registers'):
            return None

        return self.decode_data(result)

class CoilBlock:
    def __init__(self):
        self.variable_id            = [] #
        self.variable_address       = [] #
        
    
    def insert_item(self,variable_id,variable_address):
        if not self.variable_address:
            self.variable_address.append(variable_address)
            self.variable_id.append(variable_id)
        elif max(self.variable_address) < variable_address:
            self.variable_address.append(variable_address)
            self.variable_id.append(variable_id)
        elif min(self.variable_address) > variable_address:
            self.variable_address.insert(0,variable_address)
            self.variable_id.insert(0,variable_id)
        else:
            i = self.find_gap(self.variable_address,variable_address)
            if (i is not None):
                self.variable_address.insert(i,variable_address)
                self.variable_id.insert(i,variable_id)
    
    
    def request_data(self,slave):
        quantity = len(self.variable_address) # number of bits to read
        first_address = min(self.variable_address)
        
        result = slave.read_coils(first_address,quantity)
        if not hasattr(result, 'bits'):
            return None
            
        return self.decode_data(result)
        

    def decode_data(self,result):
        out = {}
        for idx in self.variable_id:
            out[idx] = result.bits.pop(0)
        return out
    
    def find_gap(self,L,value):
        for index in range(len(L)):
            if L[index] == value:
                return None
            if L[index] > value:
                return index

class DiscreteInputBlock(CoilBlock):
    def request_data(self,slave):
        quantity = len(self.variable_address) # number of bits to read
        first_address = min(self.variable_address)
        
        result = slave.read_discrete_inputs(first_address,quantity)
        if not hasattr(result, 'bits'):
            return None
            
        return self.decode_data(result)


class client:
    """
    Modbus client (Master) class
    """
    def __init__(self,client):
        self._address               = client.modbusclient.ip_address
        self._port                  = client.modbusclient.port
        self.trans_input_registers  = []
        self.trans_coils            = []
        self.trans_holding_registers = []
        self.trans_discrete_inputs  = []
        self.variables  = {}
        self._variable_config   = self._prepare_variable_config(client)
        self._not_accessible_variable = []
        self.data = []

    def _prepare_variable_config(self,client):
        
        for var in client.variable_set.filter(active=1):
            if not hasattr(var,'modbusvariable'):
                continue
            FC = var.modbusvariable.function_code_read
            if FC == 0:
                continue
            address      = var.modbusvariable.address
            bits_to_read = get_bits_by_class(var.value_class)
                
            #self.variables[var.pk] = {'value_class':var.value_class,'writeable':var.writeable,'record':var.record,'name':var.name,'adr':address,'bits':bits_to_read,'fc':FC}
            self.variables[var.pk] = RecordData(var.pk,var.name,var.value_class,var.writeable,adr=address,bits=bits_to_read,fc=FC,accessible=True)
            
            if FC == 1: # coils
                self.trans_coils.append([address,var.pk,FC])
            elif FC == 2: # discrete inputs
                self.trans_discrete_inputs.append([address,var.pk,FC])
            elif FC == 3: # holding registers
                self.trans_holding_registers.append([address,var.value_class,bits_to_read,var.pk,FC])
            elif FC == 4: # input registers
                self.trans_input_registers.append([address,var.value_class,bits_to_read,var.pk,FC])
            else:
                continue

        self.trans_discrete_inputs.sort()
        self.trans_holding_registers.sort()
        self.trans_coils.sort()
        self.trans_input_registers.sort()
        out = []
        
        # input registers
        old = -2
        regcount = 0
        for entry in self.trans_input_registers:
            if (entry[0] != old) or regcount >122:
                regcount = 0
                out.append(InputRegisterBlock()) # start new register block
            out[-1].insert_item(entry[3],entry[0],entry[1],entry[2]) # add item to block
            old = entry[0] + entry[2]/16
            regcount += entry[2]/16
        
        # holding registers
        old = -2
        regcount = 0
        for entry in self.trans_holding_registers:
            if (entry[0] != old) or regcount >122:
                regcount = 0
                out.append(HoldingRegisterBlock()) # start new register block
            out[-1].insert_item(entry[3],entry[0],entry[1],entry[2]) # add item to block
            old = entry[0] + entry[2]/16
            regcount += entry[2]/16
        
        # coils
        old = -2
        for entry in self.trans_coils:
            if (entry[0] != old+1):
                out.append(CoilBlock()) # start new coil block
            out[-1].insert_item(entry[1],entry[0])
            old = entry[0]
        #  discrete inputs
        old = -2
        for entry in self.trans_discrete_inputs:
            if (entry[0] != old+1):
                out.append(DiscreteInputBlock()) # start new coil block
            out[-1].insert_item(entry[1],entry[0])
            old = entry[0]
        return out


    def _connect(self):
        """
        connect to the modbus slave (server)
        """
        self.slave = ModbusClient(self._address,int(self._port))
        status = self.slave.connect()
        return status
        
    
    
    def _disconnect(self):
        """
        close the connection to the modbus slave (server)
        """
        self.slave.close()
    
    def request_data(self,timestamp):
        """
    
        """
        if not self._connect():
            return []
        for register_block in self._variable_config:
            result = register_block.request_data(self.slave)
            if result is None:
                self._disconnect()
                self._connect()
                result = register_block.request_data(self.slave)
            
            if result is not None:
                for variable_id in register_block.variable_id:
                    self.variables[variable_id].update_value(result[variable_id],timestamp)
                    if not self.variables[variable_id].accessible:
                        log.error(("variable with id: %d is now accessible")%(variable_id))
                        self.variables[variable_id].accessible = True
                
            else:
                for variable_id in register_block.variable_id:
                    if self.variables[variable_id].accessible:
                        log.error(("variable with id: %d is not accessible")%(variable_id))
                        self.variables[variable_id].accessible = False
                        self.variables[variable_id].update_value(None,timestamp)
            
        self._disconnect()
        return self.variables.values()
    
    def write_data(self,variable_id, value):
        """
        write value to single modbus register or coil
        """
        if not self.variables[variable_id].writeable:
            return False

        if self.variables[variable_id].fc == 3:
            # write register
            if 0 <= self.variables[variable_id].adr <= 65535:
                
                self._connect()
                if self.variables[variable_id].bits/16 == 1:
                    # just write the value to one register
                    self.slave.write_register(self.variables[variable_id].adr,int(value))
                else:
                    # encode it first
                    self.slave.write_registers(self.variables[variable_id].adr,list(encode_value(value,self.variables[variable_id].value_class)))
                self._disconnect()
                return True
            else:
                log.error('Modbus Address %d out of range'%self.variables[variable_id].adr)
                return False
        elif self.variables[variable_id].fc == 1:
            # write coil
            if 0 <= self.variables[variable_id].adr <= 65535:
                self._connect()
                self.slave.write_coil(self.variables[variable_id].adr,bool(value))
                self._disconnect()
                return True
            else:
                log.error('Modbus Address %d out of range'%self.variables[variable_id].adr)
        else:
            log.error('wrong function type %d'%self.variables[variable_id].fc)
            return False

