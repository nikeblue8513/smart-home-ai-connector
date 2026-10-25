import unittest
from connector_catalog import full_snapshot, native_data, actions_for


class CatalogTests(unittest.TestCase):
    def fixture(self):
        entities = [
            {'entity_id':'binary_sensor.smoke','device_id':'nest'},
            {'entity_id':'binary_sensor.co','device_id':'nest'},
            {'entity_id':'binary_sensor.battery','device_id':'nest','entity_category':'diagnostic'},
            {'entity_id':'sensor.last_test','device_id':'nest','entity_category':'diagnostic'},
            {'entity_id':'switch.pathlight','device_id':'nest','entity_category':'config','hidden_by':'user'},
            {'entity_id':'sensor.disabled','device_id':'nest','disabled_by':'integration'},
        ]
        states = [{'entity_id': e['entity_id'], 'state':'off','attributes':{'nested':{'detail':'value'},'access_token':'secret','entity_picture':'/api/camera?token=private&width=500'}} for e in entities[:-1]]
        states[3]['state'] = '2026-10-25T01:00:00Z'
        services = {'switch':{'turn_on':{'target':{'entity':[{'domain':'switch'}]},'fields':{}}}}
        return full_snapshot({},states,{'entities':entities,'devices':[{'id':'nest','manufacturer':'Google','serial_number':'123','sw_version':'5'}], 'services':services},'a'*32,'0.8.0')

    def test_retains_all_entities_settings_and_nested_readings(self):
        snapshot=self.fixture()
        self.assertEqual(snapshot['schema_version'],3)
        detail=snapshot['details']['nest']
        self.assertEqual(len(detail['entities']),6)
        self.assertEqual(detail['entities'][3]['state'],'2026-10-25T01:00:00Z')
        self.assertEqual(detail['entities'][0]['attributes']['nested'],{'detail':'value'})
        self.assertEqual(detail['hardware']['serial_number'],'123')
        self.assertEqual(detail['entities'][-1]['state'],'disabled')
        self.assertEqual(detail['entities'][-1]['actions'],[])
        self.assertEqual(detail['entities'][4]['actions'][0]['id'],'switch.turn_on')

    def test_credentials_are_redacted_with_visible_coverage(self):
        import json
        detail=self.fixture()['details']['nest']
        self.assertNotIn('private',json.dumps(detail))
        self.assertNotIn('secret',json.dumps(detail))
        self.assertTrue(detail['omitted'])
        self.assertIn('width=500',detail['entities'][0]['attributes']['entity_picture'])

    def test_actions_follow_domain_features_and_attribute_filters(self):
        services = {
            'fan': {'set_percentage': {
                'target': {'entity': {'domain':'fan', 'supported_features':[1]}},
                'fields': {
                    'percentage': {'required': True, 'selector': {'number': {'min':0,'max':100}}},
                    'extra': {'fields': {'preset': {'filter': {'supported_features':[8]}, 'selector': {'text': {}}}}}
                }
            }},
            'homeassistant': {'restart': {'fields': {}}}
        }
        entity={'entity_id':'fan.kitchen'}
        self.assertEqual(actions_for(entity,{'supported_features':0},services),[])
        actions=actions_for(entity,{'supported_features':1},services)
        self.assertEqual(len(actions),1)
        self.assertNotIn('preset',actions[0]['fields'])
        catalog={'fan.kitchen':{'state':'on','actions':actions}}
        cmd={'entity_id':'fan.kitchen','domain':'fan','service':'set_percentage','parameters':{'percentage':40}}
        self.assertEqual(native_data(cmd,catalog)[0],{'entity_id':'fan.kitchen','percentage':40})
        for params in ({'percentage':101},{},{'percentage':40,'entity_id':'fan.other'}):
            with self.assertRaises(ValueError):native_data({**cmd,'parameters':params},catalog)
        with self.assertRaises(ValueError):native_data({**cmd,'service':'turn_on'},catalog)
        with self.assertRaises(ValueError):native_data(cmd,{'fan.kitchen':{**catalog['fan.kitchen'],'state':'unavailable'}})

    def test_lock_permission_and_target_binding_are_enforced(self):
        action={'id':'lock.unlock','fields':{}}
        entity={'state':'locked','actions':[action]}
        cmd={'entity_id':'lock.front','domain':'lock','service':'unlock','parameters':{}}
        with self.assertRaises(ValueError):native_data(cmd,{'lock.front':entity})
        self.assertEqual(native_data(cmd,{'lock.front':entity},True)[0],{'entity_id':'lock.front'})
        with self.assertRaises(ValueError):native_data({**cmd,'entity_id':'lock.other'},{'lock.front':entity},True)

    def test_integration_device_actions_bind_the_registered_hardware(self):
        e={'entity_id':'sensor.motion','device_id':'hardware-id','platform':'example'}
        services={'example':{'configure':{'fields':{'device_id':{'required':True,'selector':{'device':{'integration':'example'}}},'sensitivity':{'required':True,'selector':{'number':{'min':0,'max':5}}}}}}}
        actions=actions_for(e,{},services,{})
        self.assertEqual(actions[0]['binding'],'device_id')
        self.assertNotIn('device_id',actions[0]['fields'])
        data,_=native_data({'entity_id':e['entity_id'],'domain':'example','service':'configure','parameters':{'sensitivity':2}},{e['entity_id']:{'registry':e,'state':'clear','actions':actions}})
        self.assertEqual(data,{'device_id':'hardware-id','sensitivity':2})

    def test_unknown_button_state_does_not_disable_press(self):
        data,_=native_data({'entity_id':'button.identify','domain':'button','service':'press','parameters':{}},{'button.identify':{'state':'unknown','actions':[{'id':'button.press','fields':{}}]}})
        self.assertEqual(data,{'entity_id':'button.identify'})
