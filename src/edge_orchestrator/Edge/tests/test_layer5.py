import unittest
import numpy as np
from edge_orchestrator.Edge.layer5_ai import encode_input,decode_output,class_scores,letterbox_rgb
from edge_orchestrator.Edge.pipeline_edge_ai import Pipeline

def detail(dtype, scale, zero):
    return dict(dtype=dtype,quantization_parameters=dict(scales=[scale],zero_points=[zero]))

class Layer5Tests(unittest.TestCase):
    def test_input_quantization(self):
        image=np.array([0,255],np.uint8)
        np.testing.assert_array_equal(encode_input(image,detail(np.int8,1/255,-128)),[-128,127])
        np.testing.assert_array_equal(encode_input(image,detail(np.uint8,1/255,0)),[0,255])
        np.testing.assert_allclose(encode_input(image,dict(dtype=np.float32)),[0,1])

    def test_output_dequantization(self):
        output=np.array([-128,0,127],np.int8)
        np.testing.assert_allclose(decode_output(output,detail(np.int8,1/255,-128)),
                                   [0,128/255,1],rtol=1e-6)

    def test_both_layouts_and_boxes_excluded(self):
        output=np.zeros((1,84,100),np.float32)
        output[:,:4]=999
        output[0,9,3]=.9
        scores=class_scores(output,80)
        self.assertAlmostEqual(float(scores.max()),.9,places=6)
        np.testing.assert_array_equal(scores,class_scores(output.transpose(0,2,1),80))

    def test_bad_output_and_quantization_fail(self):
        with self.assertRaises(ValueError):
            class_scores(np.zeros((1,300,6)),80)
        with self.assertRaises(ValueError):
            encode_input(np.array([255],np.uint8),detail(np.int8,0,0))

    def test_rgb_and_letterbox(self):
        roi=np.zeros((10,20,3),np.uint8)
        roi[:]=(1,2,3)
        image=letterbox_rgb(roi,20,20)
        np.testing.assert_array_equal(image[10,10],[3,2,1])
        np.testing.assert_array_equal(image[0,0],[114,114,114])

    def test_pipeline_gates_ai_and_cooldown(self):
        class Fake:
            calls=0
            def infer(self,roi):
                self.calls+=1
                assert roi.shape==(300,300,3)
                return {'detected':True}
        fake=Fake()
        pipeline=Pipeline(fake,clock=lambda:0.)
        for i in range(12):
            frame=np.zeros((480,640,3),np.uint8)
            frame[140:260,180+(i%6)*15:300+(i%6)*15]=255
            pipeline.step(frame)
        self.assertEqual(fake.calls,1)

    def test_outside_roi_never_calls_ai(self):
        class Fail:
            def infer(self,roi):
                raise AssertionError('AI must not run')
        pipeline=Pipeline(Fail())
        for i in range(12):
            frame=np.zeros((480,640,3),np.uint8)
            frame[5:90,5+(i%2)*20:85+(i%2)*20]=255
            _,result=pipeline.step(frame)
            self.assertFalse(result['triggered'])

if __name__=='__main__':
    unittest.main()
