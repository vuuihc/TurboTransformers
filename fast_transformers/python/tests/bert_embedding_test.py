import unittest

import contexttimer
import fast_transformers
import onnxruntime.backend as backend
import torch
import torch.jit
import torch.onnx
from transformers import BertTokenizer
from transformers.modeling_bert import BertEmbeddings, BertConfig


def create_test_bert_emb(batch_size: int, seq_length: int):
    class TestBertEmbedding(unittest.TestCase):
        def setUp(self) -> None:
            torch.set_grad_enabled(False)
            self.tokenizer = BertTokenizer.from_pretrained("bert-base-chinese")
            cfg = BertConfig(
                vocab_size_or_config_json_file=self.tokenizer.vocab_size)
            self.torch_embedding = BertEmbeddings(cfg)
            self.torch_embedding.eval()
            torch.onnx.export(
                self.torch_embedding,
                (torch.ones(size=(batch_size, seq_length), dtype=torch.long),
                 torch.ones(size=(batch_size, seq_length), dtype=torch.long),
                 torch.ones(size=(batch_size, seq_length), dtype=torch.long)),
                f="bert-emb.onnx",
                output_names=['emb'])

            if not backend.supports_device('MKL-DNN'):
                self.onnx_embedding = backend.prepare("bert-emb.onnx",
                                                      device='CPU')
            else:
                self.onnx_embedding = backend.prepare("bert-emb.onnx",
                                                      device='MKL-DNN')
            backend.prepare(self.onnx_embedding, "CPU-MKL-DNN")

            self.torch_script_embedding = torch.jit.trace(
                self.torch_embedding,
                (torch.ones(size=(batch_size, seq_length), dtype=torch.long),
                 torch.ones(size=(batch_size, seq_length), dtype=torch.long),
                 torch.ones(size=(batch_size, seq_length), dtype=torch.long)))
            self.ft_embedding = fast_transformers.BertEmbeddings.from_torch(
                self.torch_embedding)

        def test_embedding(self):
            num_iter = 10
            input_ids = torch.randint(low=0,
                                      high=self.tokenizer.vocab_size - 1,
                                      size=(batch_size, seq_length),
                                      dtype=torch.long)
            position_ids = torch.arange(seq_length,
                                        dtype=torch.long,
                                        device=input_ids.device)
            # position_ids = position_ids.unsqueeze(0).expand_as(input_ids) #will cause bug
            position_ids = position_ids.repeat(batch_size, 1)
            token_type_ids = torch.zeros_like(input_ids, dtype=torch.long)

            # warming up.
            self.torch_embedding(input_ids, token_type_ids, position_ids)
            self.torch_script_embedding(input_ids, token_type_ids,
                                        position_ids)

            onnx_inputs = [
                input_ids.numpy(),
                token_type_ids.numpy(),
                position_ids.numpy()
            ]
            self.onnx_embedding.run(onnx_inputs)
            with contexttimer.Timer() as t:
                for it in range(num_iter):
                    self.onnx_embedding.run(onnx_inputs)
            print(
                f'BertEmb({batch_size}, {seq_length:03}) ONNX (with mkl-dnn) QPS {num_iter / t.elapsed}'
            )

            with contexttimer.Timer() as t:
                for it in range(num_iter):
                    self.torch_embedding(input_ids, token_type_ids,
                                         position_ids)
            print(
                f'BertEmb({batch_size}, {seq_length:03}) Plain PyTorch QPS {num_iter / t.elapsed}'
            )

            with contexttimer.Timer() as t:
                for it in range(num_iter):
                    self.torch_script_embedding(input_ids, token_type_ids,
                                                position_ids)
            print(
                f'BertEmb({batch_size}, {seq_length:03}) TorchScript(i.e., jit) QPS {num_iter / t.elapsed}'
            )

            torch_result = self.torch_embedding(input_ids, token_type_ids,
                                                position_ids)
            ft_result = self.ft_embedding(input_ids, position_ids,
                                          token_type_ids)
            with contexttimer.Timer() as t:
                for it in range(num_iter):
                    ft_result = self.ft_embedding(input_ids, position_ids,
                                                  token_type_ids)
            self.assertTrue(
                torch.max(torch.abs(torch_result - ft_result)) < 1e-5)
            print(
                f'BertEmb({batch_size}, {seq_length:03}) FastTransform QPS {num_iter / t.elapsed}'
            )

    globals(
    )[f"TestBertEmbedding{batch_size}_{seq_length:03}"] = TestBertEmbedding


for batch_size in [1, 2]:
    for seq_length in [10, 20, 40, 80, 100, 120]:
        create_test_bert_emb(batch_size, seq_length)
# create_test_bert_emb(2, 10)
if __name__ == '__main__':
    unittest.main()