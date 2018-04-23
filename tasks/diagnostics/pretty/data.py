import glob
import io
import tarfile
import urllib.request

import matplotlib

from tasks.diagnostics.data import greedy, dist_squared

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import random
import tensorflow as tf
import numpy as np
from PIL import Image
import os
import json
from os.path import basename


def fig2array(fig):
    with io.BytesIO() as buf:  # this is pretty stupid but it was the only way I could get it rendered with anti-aliasing
        fig.savefig(buf, format='png')
        plt.close(fig)
        buf.seek(0)
        return np.array(Image.open(buf))


class PrettyClevr:
    url = "https://www.dropbox.com/s/3jaeq1ugcfs4jf3/pretty-clevr.tgz?dl=1"
    base_dir = (os.environ.get('DATA_DIR') or "/tmp")
    data_dir = base_dir + "/pretty-clevr"
    tgz_fname = base_dir + "/pretty-clevr.tgz"

    def __init__(self):
        if not os.path.exists(self.data_dir):
            print("Downloading data...")

            urllib.request.urlretrieve(self.url, self.tgz_fname)
            with tarfile.open(self.tgz_fname, "r:gz") as f:
                f.extractall(self.base_dir)

        with open(self.data_dir + '/train/dict.json') as fp:
            self.s2i = json.load(fp)
            self.i2s = {i: s for s, i in self.s2i.items()}

        self.train = self.load_data(self.data_dir + '/train', self.s2i)
        self.dev = self.load_data(self.data_dir + '/dev', self.s2i)
        self.test = self.load_data(self.data_dir + '/test', self.s2i)

    @staticmethod
    def load_data(data_dir, s2i):
        print("Loading %s..." % data_dir)

        with open(data_dir + '/questions.csv') as qf:
            questions = [l.strip().split(", ") for l in qf.readlines()]

        images = {basename(img_fname): np.array(Image.open(img_fname).convert("RGB")) for img_fname in glob.glob(data_dir + '/images/*.png')}
        all_objects = {}
        for obj_fname in glob.glob(data_dir + '/states/*.json'):
            with open(obj_fname) as fp:
                objects = json.load(fp)
                positions = np.array([o['p'] for o in objects])
                colors = [s2i[o['c']] for o in objects]
                markers = [s2i[o['m']] for o in objects]
                all_objects[basename(obj_fname)] = (positions, colors, markers)

        return questions, images, all_objects

    def output_types(self):
        return tf.uint8, tf.float32, tf.int32, tf.int32, tf.int32, tf.int32, tf.int32

    def output_shapes(self):
        return (128, 128, 3), (8, 2), (8,), (8,), (), (), ()

    def train_generator(self):
        return self.sample_generator(self.train)

    def dev_generator(self):
        return self.sample_generator(self.dev)

    def test_generator(self):
        return self.sample_generator(self.test, n_repeat=1, augment=False)

    def sample_generator(self, set, n_repeat=-1, augment=True):
        questions, images, objects = set
        count = 0
        while count < n_repeat or n_repeat == -1:
            for img_fname, json_name, anchor, n_jumps, target in random.sample(questions, len(questions)):
                img = images[img_fname]
                positions, colors, markers = objects[json_name]

                if augment:
                    scaling_matrix = (2 ** (2 * np.random.rand() - 1)) * np.identity(2, np.float32)
                    angle = np.random.rand() * 2 * np.pi
                    rotation_matrix = np.array([[np.cos(angle), np.sin(angle)], [-np.sin(angle), np.cos(angle)]])
                    positions = np.dot(positions, scaling_matrix)
                    positions = np.dot(positions, rotation_matrix)

                yield img, positions, colors, markers, self.s2i[anchor], int(n_jumps), self.s2i[target]
            count += 1


class PrettyClevrGenerator:
    colors = ['blue', 'green', 'red', 'cyan', 'magenta', 'yellow', 'black', 'gray']
    markers = ['o', 'v', '^', '<', '>', 's', 'P', 'X']
    s2i = {v: i for i, v in enumerate(colors + markers)}
    i2s = {v: k for k, v in s2i.items()}
    n_input_output = len(s2i)

    def __init__(self, n):
        assert n <= len(self.colors), "Not enough colors defined."
        assert n <= len(self.markers), "Not enough markers defined."
        self.n = n
        self.r = 0.1

    def sample_generator(self):
        while True:
            objects = [{'p': p.tolist(), 'c': c, 'm': m} for p, c, m in zip(self.get_object_positions(), random.sample(self.colors, len(self.colors)), self.markers)]
            img = self._render(objects)
            questions = []
            for start_idx in range(self.n):
                path = greedy(np.array([o['p'] for o in objects]), self.n - 1, start_idx)
                for n_jumps in range(self.n):
                    for color_anchor in [True, False]:
                        target_obj = path[n_jumps]
                        if color_anchor:
                            anchor = objects[start_idx]['c']
                            target = objects[target_obj]['m']
                        else:
                            anchor = objects[start_idx]['m']
                            target = objects[target_obj]['c']

                        questions.append((anchor, n_jumps, target))

            yield objects, img, questions

    def get_object_positions(self):
        objects = [np.random.uniform(size=(2,))]
        while len(objects) < self.n:
            o = np.random.uniform(size=(2,))
            if np.min(dist_squared(o, np.array(objects))) > self.r:
                objects.append(o)
        return objects

    def _render(self, objects):
        fig = plt.figure(figsize=(1.28, 1.28), frameon=False)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.axis('off')
        ax.set_xlim(0 - self.r, 1 + self.r)
        ax.set_ylim(0 - self.r, 1 + self.r)
        for o in objects:
            x, y = o['p']
            ax.scatter(x, y, c=o['c'], marker=o['m'])

        return fig2array(fig)

    def generate(self, n, folder):
        os.mkdir(folder)
        os.mkdir(folder + "/states")
        os.mkdir(folder + "/images")

        gen = self.sample_generator()
        with open(folder + '/dict.json', 'w') as fp:
            json.dump(self.s2i, fp)

        with open(folder + '/questions.csv', 'w') as qf:
            for i in range(n):
                objects, img, questions = next(gen)
                png_name = '%05d.png' % i
                json_name = '%05d.json' % i
                with open(folder + '/states/' + json_name, 'w') as fp:
                    json.dump(objects, fp)
                Image.fromarray(img).save(folder + '/images/' + png_name)
                qf.writelines(["%s, %s, %s, %d, %s\n" % (png_name, json_name, a, b, c) for (a, b, c) in questions])


if __name__ == '__main__':
    d = PrettyClevrGenerator(8)
    d.generate(100000, 'train')
    d.generate(1000, 'dev')
    d.generate(1000, 'test')
