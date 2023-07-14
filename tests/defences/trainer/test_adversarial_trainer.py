# MIT License
#
# Copyright (C) The Adversarial Robustness Toolbox (ART) Authors 2018
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
# persons to whom the Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
# Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
# WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
from __future__ import absolute_import, division, print_function, unicode_literals

import logging
import pytest

import numpy as np
import pickle

import torch
from art.attacks.evasion.fast_gradient import FastGradientMethod
from art.attacks.evasion.deepfool import DeepFool
from art.data_generators import DataGenerator
from art.defences.trainer.adversarial_trainer import AdversarialTrainer
# from art.utils import load_mnist
from art.estimators.classification import PyTorchClassifier, TensorFlowV2Classifier, KerasClassifier
from art.utils import load_mnist

from tests.utils import master_seed, get_image_classifier_tf, get_image_classifier_pt

logger = logging.getLogger(__name__)

BATCH_SIZE = 10
NB_TRAIN = 100
NB_TEST = 100


@pytest.fixture()
def get_mnist_classifier(framework, image_dl_estimator):

    def _get_classifier():
        if framework == "pytorch":
            import torch

            classifier = get_image_classifier_pt()

        elif framework == "tensorflow2":
            import tensorflow as tf
            classifier, _ = get_image_classifier_tf()

        elif framework in ("keras", "kerastf"):
            import tensorflow as tf
            from tensorflow.keras import layers, Sequential

            if tf.__version__[0] == "2":
                tf.compat.v1.disable_eager_execution()

            model = Sequential()
            model.add(layers.Conv2D(1, kernel_size=(7, 7), activation="relu", input_shape=(28, 28, 1)))
            model.add(layers.MaxPooling2D(pool_size=(4, 4)))
            model.add(layers.Flatten())
            model.add(layers.Dense(10))
            model.compile(optimizer="adam", loss="categorical_crossentropy")
            classifier = KerasClassifier(model, clip_values=(0, 1), use_logits=True)

        elif framework == "huggingface":
            import transformers
            import torch
            from art.estimators.hugging_face import HuggingFaceClassifier

            model = transformers.AutoModelForImageClassification.from_pretrained('facebook/deit-tiny-patch16-224', # takes 3 min
                                                                                 ignore_mismatched_sizes=True,
                                                                                 num_labels=10)

            print('num of parameters is ', sum(p.numel() for p in model.parameters() if p.requires_grad))
            optimizer = torch.optim.Adam(model.parameters(), lr=1e-4)

            classifier = HuggingFaceClassifier(model,
                                               loss_fn=torch.nn.CrossEntropyLoss(),
                                               optimizer=optimizer,
                                               processor=None)

        else:
            classifier = None

        return classifier

    return _get_classifier


class TestClass:

    # TODO: Original tests rely on the same classifier being trained between tests sequentially, hence making
    # TODO: a pytest global variable to track them in pytest. Consider changing the tests so that they
    # TODO: are all fully self contained.

    pytest.classifier = None
    pytest.classifier_2 = None

    @pytest.mark.only_with_platform("pytorch", "tensorflow2", "keras", "kerastf", "huggingface")
    def test_get_classifiers(self, get_mnist_classifier):
        if pytest.classifier is None:
            pytest.classifier = get_mnist_classifier()
        if pytest.classifier_2 is None:
            pytest.classifier_2 = get_mnist_classifier()

    @pytest.mark.only_with_platform("pytorch", "tensorflow2", "keras", "kerastf", "huggingface")
    def test_fit_predict(self, art_warning, get_mnist_classifier, get_default_mnist_subset, framework):

        master_seed(seed=1234)

        (x_train, y_train), (x_test, y_test), _, _ = load_mnist()
        x_train, y_train, x_test, y_test = (
            x_train[:NB_TRAIN],
            y_train[:NB_TRAIN],
            x_test[:NB_TEST],
            y_test[:NB_TEST],
        )

        if framework == 'pytorch':
            x_train = np.float32(np.moveaxis(x_train, -1, 1))
            x_test = np.float32(np.moveaxis(x_test, -1, 1))

        x_test_original = x_test.copy()

        attack = FastGradientMethod(pytest.classifier)
        x_test_adv = attack.generate(x_test)
        predictions = np.argmax(pytest.classifier.predict(x_test_adv), axis=1)
        accuracy = np.sum(predictions == np.argmax(y_test, axis=1)) / NB_TEST

        # TODO: pytorch was not checked in the original test. Why does it train differently to tf? Why does the tf
        # TODO: accuracy decrease?
        assert accuracy == 0.13

        adv_trainer = AdversarialTrainer(pytest.classifier, attack)
        adv_trainer.fit(x_train, y_train, nb_epochs=5, batch_size=128)

        # NB! x_test_adv is a transfer attack based on the original pre-training weights
        predictions_new = np.argmax(adv_trainer.predict(x_test_adv), axis=1)
        accuracy_new = np.sum(predictions_new == np.argmax(y_test, axis=1)) / NB_TEST

        if framework == 'pytorch':
            # with open("classifier_test_fit_predict_post_training", "wb") as fp:  # Pickling
            #    pickle.dump(pytest.classifier.model.state_dict(), fp)
            assert accuracy_new == 0.20
        else:
            assert accuracy_new == 0.12

        # Recompute adversarial examples.
        attack = FastGradientMethod(pytest.classifier)
        x_test_adv = attack.generate(x_test)
        predictions_new = np.argmax(adv_trainer.predict(x_test_adv), axis=1)
        accuracy_new = np.sum(predictions_new == np.argmax(y_test, axis=1)) / NB_TEST

        if framework == 'pytorch':
            assert accuracy_new == 0.12
        if framework == 'tensorflow':
            assert accuracy_new == 0.13

        pytest.final_acc_of_prior = accuracy_new
        # Check that x_test has not been modified by attack and classifier
        assert np.allclose(float(np.max(np.abs(x_test_original - x_test))), 0.0)

    def test_fit_predict_different_classifiers(self, art_warning, get_mnist_classifier, get_default_mnist_subset, framework):

        master_seed(seed=1234)

        (x_train, y_train), (x_test, y_test), _, _ = load_mnist()
        x_train, y_train, x_test, y_test = (
            x_train[:NB_TRAIN],
            y_train[:NB_TRAIN],
            x_test[:NB_TEST],
            y_test[:NB_TEST],
        )

        if framework == 'pytorch':
            x_train = np.float32(np.moveaxis(x_train, -1, 1))
            x_test = np.float32(np.moveaxis(x_test, -1, 1))

        x_test_original = x_test.copy()

        attack = FastGradientMethod(pytest.classifier)
        x_test_adv = attack.generate(x_test)
        predictions = np.argmax(pytest.classifier.predict(x_test_adv), axis=1)
        accuracy = np.sum(predictions == np.argmax(y_test, axis=1)) / NB_TEST

        # with open("classifier_test_fit_predict_post_training", "rb") as fp:  # Pickling
        #    classifier_test_fit_predict_post_training = pickle.load(fp)
        if framework == 'pytorch':
            # for w1, w2 in zip(classifier_test_fit_predict_post_training.values(), pytest.classifier.model.state_dict().values()):
            #    assert torch.allclose(w1, w2)
            assert accuracy == 0.12
        if framework == 'tensorflow2':
            assert accuracy == 0.13  # TODO, Untrained acc? Why is this not 0.12?

        assert accuracy == pytest.final_acc_of_prior

        adv_trainer = AdversarialTrainer(pytest.classifier_2, attack)
        adv_trainer.fit(x_train, y_train, nb_epochs=5, batch_size=128)

        predictions_new = np.argmax(adv_trainer.predict(x_test_adv), axis=1)
        accuracy_new = np.sum(predictions_new == np.argmax(y_test, axis=1)) / NB_TEST

        if framework == 'pytorch':
            assert accuracy_new == 0.11
        if framework == 'tensorflow2':
            assert accuracy_new == 0.32

        # Check that x_test has not been modified by attack and classifier
        np.allclose(float(np.max(np.abs(x_test_original - x_test))), 0.0)

        # fit_generator
        class MyDataGenerator(DataGenerator):
            def __init__(self, x, y, size, batch_size):
                super().__init__(size=size, batch_size=batch_size)
                self.x = x
                self.y = y
                self._size = size
                self._batch_size = batch_size

            def get_batch(self):
                ids = np.random.choice(self.size, size=min(self.size, self.batch_size), replace=False)
                return self.x[ids], self.y[ids]

        generator = MyDataGenerator(x_train, y_train, size=x_train.shape[0], batch_size=16)
        adv_trainer.fit_generator(generator, nb_epochs=5)
        adv_trainer_2 = AdversarialTrainer(pytest.classifier_2, attack, ratio=1.0)
        adv_trainer_2.fit_generator(generator, nb_epochs=5)

    def test_two_attacks(self, art_warning, get_mnist_classifier, get_default_mnist_subset, framework):

        master_seed(seed=1234)

        (x_train, y_train), (x_test, y_test), _, _ = load_mnist()
        x_train, y_train, x_test, y_test = (
            x_train[:NB_TRAIN],
            y_train[:NB_TRAIN],
            x_test[:NB_TEST],
            y_test[:NB_TEST],
        )

        if framework == 'pytorch':
            x_train = np.float32(np.moveaxis(x_train, -1, 1))
            x_test = np.float32(np.moveaxis(x_test, -1, 1))

        x_test_original = x_test.copy()

        attack1 = FastGradientMethod(estimator=pytest.classifier, batch_size=16)
        attack2 = DeepFool(classifier=pytest.classifier, max_iter=5, batch_size=16)
        x_test_adv = attack1.generate(x_test)
        predictions = np.argmax(pytest.classifier.predict(x_test_adv), axis=1)
        accuracy = np.sum(predictions == np.argmax(y_test, axis=1)) / NB_TEST

        adv_trainer = AdversarialTrainer(pytest.classifier, attacks=[attack1, attack2])
        adv_trainer.fit(x_train, y_train, nb_epochs=2, batch_size=16)

        predictions_new = np.argmax(adv_trainer.predict(x_test_adv), axis=1)
        accuracy_new = np.sum(predictions_new == np.argmax(y_test, axis=1)) / NB_TEST

        if framework == 'tensorflow2':
            assert accuracy_new == 0.14
            assert accuracy == 0.13  # Untrained acc?
        if framework == 'pytorch':
            assert accuracy_new == 0.18
            assert accuracy == 0.12

        # Check that x_test has not been modified by attack and classifier
        assert np.allclose(float(np.max(np.abs(x_test_original - x_test))), 0.0)

    def test_two_attacks_with_generator(self, art_warning, get_mnist_classifier, get_default_mnist_subset, framework):
        (x_train, y_train), (x_test, y_test), _, _ = load_mnist()
        x_train, y_train, x_test, y_test = (
            x_train[:NB_TRAIN],
            y_train[:NB_TRAIN],
            x_test[:NB_TEST],
            y_test[:NB_TEST],
        )

        if framework == 'pytorch':
            x_train = np.float32(np.moveaxis(x_train, -1, 1))
            x_test = np.float32(np.moveaxis(x_test, -1, 1))

        x_train_original = x_train.copy()
        x_test_original = x_test.copy()

        class MyDataGenerator(DataGenerator):
            def __init__(self, x, y, size, batch_size):
                super().__init__(size=size, batch_size=batch_size)
                self.x = x
                self.y = y
                self._size = size
                self._batch_size = batch_size

            def get_batch(self):
                ids = np.random.choice(self.size, size=min(self.size, self.batch_size), replace=False)
                return self.x[ids], self.y[ids]

        generator = MyDataGenerator(x_train, y_train, size=x_train.shape[0], batch_size=16)

        attack1 = FastGradientMethod(estimator=pytest.classifier, batch_size=16)
        attack2 = DeepFool(classifier=pytest.classifier, max_iter=5, batch_size=16)
        x_test_adv = attack1.generate(x_test)
        predictions = np.argmax(pytest.classifier.predict(x_test_adv), axis=1)
        accuracy = np.sum(predictions == np.argmax(y_test, axis=1)) / NB_TEST

        adv_trainer = AdversarialTrainer(pytest.classifier, attacks=[attack1, attack2])
        adv_trainer.fit_generator(generator, nb_epochs=3)

        predictions_new = np.argmax(adv_trainer.predict(x_test_adv), axis=1)
        accuracy_new = np.sum(predictions_new == np.argmax(y_test, axis=1)) / NB_TEST

        if framework == 'tensorflow2':
            # self.assertAlmostEqual(accuracy_new, 0.38, delta=0.02)
            assert 0.36 <= accuracy_new <= 0.40
            assert accuracy == 0.1
        if framework == 'pytorch':
            # self.assertAlmostEqual(accuracy_new, 0.38, delta=0.02)
            assert 0.21 <= accuracy_new <= 0.25
            assert accuracy == 0.17

        # Check that x_train and x_test has not been modified by attack and classifier
        assert np.allclose(float(np.max(np.abs(x_train_original - x_train))), 0.0)
        assert np.allclose(float(np.max(np.abs(x_test_original - x_test))), 0.0)

    def test_targeted_attack_error(self, art_warning, get_mnist_classifier, get_default_mnist_subset, framework):
        """
        Test the adversarial trainer using a targeted attack, which will currently result in a NotImplementError.

        :return: None
        """
        (x_train, y_train), (x_test, y_test), _, _ = load_mnist()
        x_train, y_train, x_test, y_test = (
            x_train[:NB_TRAIN],
            y_train[:NB_TRAIN],
            x_test[:NB_TEST],
            y_test[:NB_TEST],
        )

        if framework == 'pytorch':
            x_train = np.float32(np.moveaxis(x_train, -1, 1))
            x_test = np.float32(np.moveaxis(x_test, -1, 1))

        params = {"nb_epochs": 2, "batch_size": BATCH_SIZE}

        adv = FastGradientMethod(pytest.classifier, targeted=True)
        adv_trainer = AdversarialTrainer(pytest.classifier, attacks=adv)
        # self.assertRaises(NotImplementedError, adv_trainer.fit, x_train, y_train, **params)

'''
class TestAdversarialTrainer(unittest.TestCase):
    """
    Test cases for the AdversarialTrainer class.
    """

    @classmethod
    def setUpClass(cls):
        # MNIST
        (x_train, y_train), (x_test, y_test), _, _ = load_mnist()
        x_train, y_train, x_test, y_test = (
            x_train[:NB_TRAIN],
            y_train[:NB_TRAIN],
            x_test[:NB_TEST],
            y_test[:NB_TEST],
        )
        cls.mnist = ((x_train, y_train), (x_test, y_test))

        cls.classifier, _ = get_image_classifier_tf()
        cls.classifier_2, _ = get_image_classifier_tf()


    def test_classifier_match(self):
        attack = FastGradientMethod(self.classifier)
        adv_trainer = AdversarialTrainer(self.classifier, attack)

        self.assertEqual(len(adv_trainer.attacks), 1)
        self.assertEqual(adv_trainer.attacks[0].estimator, adv_trainer.get_classifier())

    def test_excpetions(self):
        with self.assertRaises(ValueError):
            _ = AdversarialTrainer(self.classifier, "attack")

        with self.assertRaises(ValueError):
            attack = FastGradientMethod(self.classifier)
            _ = AdversarialTrainer(self.classifier, attack, ratio=1.5)


if __name__ == "__main__":
    unittest.main()
'''