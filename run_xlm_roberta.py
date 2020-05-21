import os
from collections import Counter

import numpy as np
import pandas as pd
from glob import glob
import torch
import torch.nn as nn
from torch.utils.data import Dataset
import sklearn
import time
from datetime import datetime
from tqdm import tqdm
from transformers import XLMRobertaTokenizer
from transformers import AdamW
from nltk import sent_tokenize
import random
import albumentations
from albumentations.core.transforms_interface import BasicTransform
import gc
import re

# import nltk
# nltk.download()
from transformers import XLMRobertaForSequenceClassification

from pandarallel import pandarallel
pandarallel.initialize(nb_workers=4, progress_bar=False)

SEED = 42
MAX_LENGTH = 224

ROOT_PATH = os.path.realpath(__file__ + '/..')
BACKBONE_PATH = f'{ROOT_PATH}/xlm-roberta-large'



def seed_everything(seed):
    random.seed(seed)
    os.environ['PYTHONHASHSEED'] = str(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = True

seed_everything(SEED)

LANGS = {
    'en': 'english',
    'it': 'italian',
    'fr': 'french',
    'es': 'spanish',
    'tr': 'turkish',
    'ru': 'english',
    'pt': 'english'
}

def get_sentences(text, lang='en'):
    return sent_tokenize(text, LANGS.get(lang, 'english'))

def exclude_duplicate_sentences(text, lang='en'):
    sentences = []
    for sentence in get_sentences(text, lang):
        sentence = sentence.strip()
        if sentence not in sentences:
            sentences.append(sentence)
    return ' '.join(sentences)

def clean_text(text, lang='en'):
    text = str(text)
    text = re.sub(r'[0-9"]', '', text)
    text = re.sub(r'#[\S]+\b', '', text)
    text = re.sub(r'@[\S]+\b', '', text)
    text = re.sub(r'https?\S+', '', text)
    text = re.sub(r'\s+', ' ', text)
    text = exclude_duplicate_sentences(text, lang)
    return text.strip()

class NLPTransform(BasicTransform):
    """ Transform for nlp task."""
    @property
    def targets(self):
        return {"data": self.apply}

    def update_params(self, params, **kwargs):
        if hasattr(self, "interpolation"):
            params["interpolation"] = self.interpolation
        if hasattr(self, "fill_value"):
            params["fill_value"] = self.fill_value
        return params

    def get_sentences(self, text, lang='en'):
        return sent_tokenize(text, LANGS.get(lang, 'english'))

class ShuffleSentencesTransform(NLPTransform):
    """ Do shuffle by sentence """
    def __init__(self, always_apply=False, p=0.5):
        super(ShuffleSentencesTransform, self).__init__(always_apply, p)

    def apply(self, data, **params):
        text, lang = data
        sentences = self.get_sentences(text, lang)
        random.shuffle(sentences)
        return ' '.join(sentences), lang

class ExcludeDuplicateSentencesTransform(NLPTransform):
    """ Exclude equal sentences """

    def __init__(self, always_apply=False, p=0.5):
        super(ExcludeDuplicateSentencesTransform, self).__init__(always_apply, p)

    def apply(self, data, **params):
        text, lang = data
        sentences = []
        for sentence in self.get_sentences(text, lang):
            sentence = sentence.strip()
            if sentence not in sentences:
                sentences.append(sentence)
        return ' '.join(sentences), lang

class ExcludeNumbersTransform(NLPTransform):
    """ exclude any numbers """

    def __init__(self, always_apply=False, p=0.5):
        super(ExcludeNumbersTransform, self).__init__(always_apply, p)

    def apply(self, data, **params):
        text, lang = data
        text = re.sub(r'[0-9]', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text, lang

class ExcludeHashtagsTransform(NLPTransform):
    """ Exclude any hashtags with # """

    def __init__(self, always_apply=False, p=0.5):
        super(ExcludeHashtagsTransform, self).__init__(always_apply, p)

    def apply(self, data, **params):
        text, lang = data
        text = re.sub(r'#[\S]+\b', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text, lang

class ExcludeUsersMentionedTransform(NLPTransform):
    """ Exclude @users """

    def __init__(self, always_apply=False, p=0.5):
        super(ExcludeUsersMentionedTransform, self).__init__(always_apply, p)

    def apply(self, data, **params):
        text, lang = data
        text = re.sub(r'@[\S]+\b', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text, lang

class ExcludeUrlsTransform(NLPTransform):
    """ Exclude urls """

    def __init__(self, always_apply=False, p=0.5):
        super(ExcludeUrlsTransform, self).__init__(always_apply, p)

    def apply(self, data, **params):
        text, lang = data
        text = re.sub(r'https?\S+', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text, lang

class SynthesicOpenSubtitlesTransform(NLPTransform):
    def __init__(self, always_apply=False, p=0.5):
        super(SynthesicOpenSubtitlesTransform, self).__init__(always_apply, p)
        df = pd.read_csv(f'{ROOT_PATH}/data/open-subtitles-synthesic.csv',
                         index_col='id')[['comment_text', 'toxic', 'lang']]
        df = df[~df['comment_text'].isna()]
        df['comment_text'] = df.parallel_apply(lambda x: clean_text(x['comment_text'], x['lang']), axis=1)
        df = df.drop_duplicates(subset='comment_text')
        df['toxic'] = df['toxic'].round().astype(np.int)

        self.synthesic_toxic = df[df['toxic'] == 1].comment_text.values
        self.synthesic_non_toxic = df[df['toxic'] == 0].comment_text.values

        del df
        gc.collect()

    def generate_synthesic_sample(self, text, toxic):
        texts = [text]
        if toxic == 0:
            for i in range(random.randint(1, 5)):
                texts.append(random.choice(self.synthesic_non_toxic))
        else:
            for i in range(random.randint(0, 2)):
                texts.append(random.choice(self.synthesic_non_toxic))

            for i in range(random.randint(1, 3)):
                texts.append(random.choice(self.synthesic_toxic))
        random.shuffle(texts)
        return ' '.join(texts)

    def apply(self, data, **params):
        text, toxic = data
        text = self.generate_synthesic_sample(text, toxic)
        return text, toxic


def get_train_transforms():
    return albumentations.Compose([
        ExcludeUsersMentionedTransform(p=0.95),
        ExcludeUrlsTransform(p=0.95),
        ExcludeNumbersTransform(p=0.95),
        ExcludeHashtagsTransform(p=0.95),
        ExcludeDuplicateSentencesTransform(p=0.95),
    ], p=1.0)


def get_synthesic_transforms():
    return SynthesicOpenSubtitlesTransform(p=0.5)


train_transforms = get_train_transforms()
synthesic_transforms = get_synthesic_transforms()
tokenizer = XLMRobertaTokenizer.from_pretrained(BACKBONE_PATH)
shuffle_transforms = ShuffleSentencesTransform(always_apply=True)

class DatasetRetriever(Dataset):

    def __init__(self, labels_or_ids, comment_texts, langs, use_train_transforms=False, test=False):
        self.test = test
        self.labels_or_ids = labels_or_ids
        self.comment_texts = comment_texts
        self.langs = langs
        self.use_train_transforms = use_train_transforms

    def get_tokens(self, text):
        encoded = tokenizer.encode_plus(
            text,
            add_special_tokens=True,
            max_length=MAX_LENGTH,
            pad_to_max_length=True
        )
        return encoded['input_ids'], encoded['attention_mask']

    def __len__(self):
        return self.comment_texts.shape[0]

    def __getitem__(self, idx):
        text = self.comment_texts[idx]
        lang = self.langs[idx]
        if self.test is False:
            label  = self.labels_or_ids[idx]
            target = label

        if self.use_train_transforms:
            text, _ = train_transforms(data=(text, lang))['data']
            tokens, attention_mask = self.get_tokens(str(text))
            token_length = sum(attention_mask)
            # if token_length > 0.8 * MAX_LENGTH:
            #     text, _ = shuffle_transforms(data=(text, lang))['data']
            if token_length < 60:
                text, _ = synthesic_transforms(data=(text, label))['data']
            else:
                tokens, attention_mask = torch.tensor(tokens), torch.tensor(attention_mask)
                return target, tokens, attention_mask

        tokens, attention_mask = self.get_tokens(str(text))
        tokens, attention_mask = torch.tensor(tokens), torch.tensor(attention_mask)

        if self.test is False:
            return target, tokens, attention_mask
        return self.labels_or_ids[idx], tokens, attention_mask

    def get_labels(self):
        return list(np.char.add(self.labels_or_ids.astype(str), self.langs))


df_train = pd.read_csv(f'{ROOT_PATH}/data/train_data.csv')
print(df_train.shape)
# df_train = df_train.sample(2000000)
print(Counter(df_train['toxic']))
train_data_len = df_train.shape[0]
train_dataset = DatasetRetriever(
    labels_or_ids=df_train['toxic'].values,
    comment_texts=df_train['comment_text'].values,
    langs=df_train['lang'].values,
    use_train_transforms=True,
)

del df_train
gc.collect()

for targets, tokens, attention_masks in train_dataset:
    print(targets)
    print(tokens)
    print(attention_masks)
    break



np.unique(train_dataset.get_labels())

df_val = pd.read_csv(f'{ROOT_PATH}/data/validation.csv',
                     index_col='id')
val_len = df_val.shape[0]
validation_tune_dataset = DatasetRetriever(
    labels_or_ids=df_val['toxic'].values,
    comment_texts=df_val['comment_text'].values,
    langs=df_val['lang'].values,
    use_train_transforms=True,
)

df_val['comment_text'] = df_val.parallel_apply(lambda x: clean_text(x['comment_text'], x['lang']), axis=1)

validation_dataset = DatasetRetriever(
    labels_or_ids=df_val['toxic'].values,
    comment_texts=df_val['comment_text'].values,
    langs=df_val['lang'].values,
    use_train_transforms=False,
)

del df_val
gc.collect()

for targets, tokens, attention_masks in validation_dataset:
    print(targets)
    print(tokens)
    print(attention_masks)
    break

df_test = pd.read_csv(f'{ROOT_PATH}/data/test.csv', index_col='id')
df_test['comment_text'] = df_test.parallel_apply(lambda x: clean_text(x['content'], x['lang']), axis=1)
test_len = df_test.shape[0]
test_dataset = DatasetRetriever(
    labels_or_ids=df_test.index.values,
    comment_texts=df_test['comment_text'].values,
    langs=df_test['lang'].values,
    use_train_transforms=False,
    test=True
)

del df_test
gc.collect()

for ids, tokens, attention_masks in test_dataset:
    print(ids)
    print(tokens)
    print(attention_masks)
    break

class RocAucMeter(object):
    def __init__(self):
        self.reset()

    def reset(self):
        self.y_true = np.array([0, 1])
        self.y_pred = np.array([0.5, 0.5])
        self.score = 0

    def update(self, y_true, y_pred):
        y_true = y_true.cpu().numpy()
        y_pred = nn.functional.softmax(y_pred, dim=1).data.cpu().numpy()[:, 1]
        self.y_true = np.hstack((self.y_true, y_true))
        self.y_pred = np.hstack((self.y_pred, y_pred))
        self.score = sklearn.metrics.roc_auc_score(self.y_true[-10000:], self.y_pred[-10000:], labels=np.array([0, 1]))

    @property
    def avg(self):
        return self.score


class AverageMeter(object):
    """Computes and stores the average and current value"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count


class LabelSmoothing(nn.Module):
    def __init__(self, smoothing=0.1):
        super(LabelSmoothing, self).__init__()
        self.confidence = 1.0 - smoothing
        self.smoothing = smoothing

    def forward(self, x, target):
        if self.training:
            x = x.float()
            target = target.float()
            logprobs = torch.nn.functional.log_softmax(x, dim=-1)
            nll_loss = -logprobs * target
            nll_loss = nll_loss.sum(-1)
            smooth_loss = -logprobs.mean(dim=-1)
            loss = self.confidence * nll_loss + self.smoothing * smooth_loss
            return loss.mean()
        else:
            return torch.nn.functional.cross_entropy(x, target)


import warnings
warnings.filterwarnings("ignore")

class TPUFitter:

    def __init__(self, model, device, config):
        if not os.path.exists('node_submissions'):
            os.makedirs('node_submissions')

        if not os.path.exists('node-ckpts'):
            os.makedirs('node-ckpts')
        if not os.path.exists('logs'):
            os.makedirs('logs')
        self.best_score = 0
        self.config = config
        self.epoch = 0
        node_count = len(glob('logs/*.txt'))
        self.log_path = f'logs/log{node_count}.txt'
        self.final_scores = RocAucMeter()
        self.model = model
        self.device = device

        param_optimizer = list(self.model.named_parameters())
        no_decay = ['bias', 'LayerNorm.bias', 'LayerNorm.weight']
        optimizer_grouped_parameters = [
            {'params': [p for n, p in param_optimizer if not any(nd in n for nd in no_decay)], 'weight_decay': 0.001},
            {'params': [p for n, p in param_optimizer if any(nd in n for nd in no_decay)], 'weight_decay': 0.0}
        ]

        self.optimizer = AdamW(optimizer_grouped_parameters, lr=config.lr)
        self.scheduler = config.SchedulerClass(self.optimizer, **config.scheduler_params)

    def fit(self, train_loader, validation_loader,test_loader):
        for e in range(self.config.n_epochs):
            print(f'Epoch:{e+1}/{self.config.n_epochs}')
            if self.config.verbose:
                lr = self.optimizer.param_groups[0]['lr']
                timestamp = datetime.utcnow().isoformat()
                self.log(f'\n{timestamp}\nLR: {lr}\n')

            t = time.time()
            losses, final_scores = self.train_one_epoch(train_loader)

            self.log(
                f'[RESULT]: Train. Epoch: {self.epoch}, loss: {losses.avg:.5f}, final_score: {final_scores.avg:.5f}, time: {(time.time() - t):.5f}\n')

            t = time.time()
            losses, final_scores = self.validation(validation_loader)

            self.log(
                f'[RESULT]: Validation. Epoch: {self.epoch}, loss: {losses.avg:.5f}, final_score: {final_scores.avg:.5f}, time: {(time.time() - t):.5f}\n')

            if self.config.validation_scheduler:
                self.scheduler.step(metrics=final_scores.avg)
            self.epoch += 1
            if final_scores.avg > self.best_score:
                self.log(f'flnal_score improved from {self.best_score} to {final_scores.avg},save model')
                self.best_score = final_scores.avg
                node_count = len(glob('node-ckpts/*.bin'))
                self.save(f'node-ckpts/best_model{node_count}.bin')
                self.run_inference(test_loader=test_loader)
            else:
                self.log(f'flnal_score did not improved from {self.best_score}')


    def run_tuning_and_inference(self, test_loader, validation_tune_loader):
        for e in range(1):
            self.optimizer.param_groups[0]['lr'] = self.config.lr

            losses, final_scores = self.train_one_epoch(validation_tune_loader)
            if final_scores.avg > self.best_score:
                self.best_score = final_scores.avg
                self.save('node-ckpts/best_model.bin')
            self.run_inference(test_loader)

    def validation(self, val_loader):
        print('run_validation\n')
        self.model.eval()
        losses = AverageMeter()
        final_scores = RocAucMeter()
        bar = tqdm(range(int(val_len / self.config.batch_size + 1)))
        for step, (targets, inputs, attention_masks) in zip(bar, val_loader):
            with torch.no_grad():
                inputs = inputs.to(self.device, dtype=torch.long)
                attention_masks = attention_masks.to(self.device, dtype=torch.long)
                targets = targets.to(self.device, dtype=torch.long)
                outputs = self.model(input_ids = inputs, attention_mask = attention_masks,labels = targets)
                loss = outputs[0]
                batch_size = inputs.size(0)
                final_scores.update(targets, outputs[1])
                losses.update(loss.detach().item(), batch_size)
        return losses, final_scores

    def train_one_epoch(self, train_loader):
        self.model.train()
        losses = AverageMeter()
        t = time.time()
        bar = tqdm(range(int(train_data_len/self.config.batch_size)))
        for step,(targets, inputs, attention_masks) in zip(bar,train_loader):
            if self.config.verbose:
                if step % self.config.verbose_step == 0:
                    self.log(
                        f'Train Step {step}, loss: ' + \
                        f'{losses.avg:.5f}, final_score: {self.final_scores.avg:.5f}, ' + \
                        f'time: {(time.time() - t):.5f}\n'
                    )
            with01 = sum(targets)
            if with01 < self.config.batch_size*0.15:
                continue
            inputs = inputs.to(self.device, dtype=torch.long)
            attention_masks = attention_masks.to(self.device, dtype=torch.long)
            targets = targets.to(self.device, dtype=torch.long)
            outputs = self.model(input_ids = inputs, attention_mask = attention_masks,labels = targets)
            loss = outputs[0]
            logits = outputs[1]
            batch_size = inputs.size(0)
            self.final_scores.update(targets, logits)
            losses.update(loss.detach().item(), batch_size)
            bar.set_description(f'loss:{round(losses.avg,4)};RocAuc:{round(self.final_scores.avg, 4)}')
            loss.backward()
            self.optimizer.step()
            self.optimizer.zero_grad()
            if self.config.step_scheduler:
                self.scheduler.step()
        self.model.eval()
        return losses, self.final_scores

    def run_inference(self, test_loader):
        print('run_inference\n')
        node_count = len(glob('node-ckpts/*.bin'))
        print(f'load model from ./node-ckpts/best_model{node_count-1}.bin')
        self.model.load_state_dict(torch.load(f'./node-ckpts/best_model{node_count-1}.bin'))
        self.model.eval()
        result = {'id': [], 'toxic': []}
        for step, (ids, inputs, attention_masks) in tqdm(enumerate(test_loader)):
            with torch.no_grad():
                inputs = inputs.to(self.device, dtype=torch.long)
                attention_masks = attention_masks.to(self.device, dtype=torch.long)
                outputs = self.model(input_ids = inputs, attention_mask = attention_masks)[0]
                toxics = nn.functional.softmax(outputs, dim=1).data.cpu().numpy()[:, 1]
            result['id'].extend(ids.cpu().numpy())
            result['toxic'].extend(toxics)
        result = pd.DataFrame(result)
        node_count = len(glob('node_submissions/*.csv'))
        result.to_csv(f'node_submissions/submission_{node_count}_{datetime.utcnow().microsecond}.csv',
                      index=False)

    def save(self, path):
        torch.save(self.model.state_dict(), path)

    def log(self, message):
        with open(self.log_path, 'a+') as logger:
            logger.write(f'{message}')



net = XLMRobertaForSequenceClassification.from_pretrained(BACKBONE_PATH)

class TrainGlobalConfig:
    num_workers = 0
    batch_size = 7
    n_epochs = 3
    lr = 2e-5

    # -------------------
    verbose = True
    verbose_step = 50
    # -------------------

    # --------------------
    step_scheduler = False  # do scheduler.step after optimizer.step
    validation_scheduler = True  # do scheduler.step after validation stage loss
    SchedulerClass = torch.optim.lr_scheduler.ReduceLROnPlateau
    scheduler_params = dict(
        mode='max',
        factor=0.7,
        patience=0,
        verbose=False,
        threshold=0.0001,
        threshold_mode='abs',
        cooldown=0,
        min_lr=1e-8,
        eps=1e-08
    )


def _mp_fn(rank):
    device = 'cuda'
    net.to(device)

    train_sampler = torch.utils.data.RandomSampler(
        train_dataset
    )
    train_loader = torch.utils.data.DataLoader(
        train_dataset,
        batch_size=TrainGlobalConfig.batch_size,
        sampler=train_sampler,
        pin_memory=False,
        drop_last=True,
        num_workers=TrainGlobalConfig.num_workers,
    )
    validation_sampler = torch.utils.data.SequentialSampler(
        validation_dataset
    )
    validation_loader = torch.utils.data.DataLoader(
        validation_dataset,
        batch_size=TrainGlobalConfig.batch_size,
        sampler=validation_sampler,
        pin_memory=False,
        drop_last=False,
        num_workers=TrainGlobalConfig.num_workers
    )
    validation_tune_sampler = torch.utils.data.SequentialSampler(
        validation_tune_dataset
    )
    validation_tune_loader = torch.utils.data.DataLoader(
        validation_tune_dataset,
        batch_size=TrainGlobalConfig.batch_size,
        sampler=validation_tune_sampler,
        pin_memory=False,
        drop_last=False,
        num_workers=TrainGlobalConfig.num_workers
    )
    test_sampler = torch.utils.data.SequentialSampler(
        test_dataset
    )
    test_loader = torch.utils.data.DataLoader(
        test_dataset,
        batch_size=TrainGlobalConfig.batch_size,
        sampler=test_sampler,
        pin_memory=False,
        drop_last=False,
        num_workers=TrainGlobalConfig.num_workers
    )

    if rank == 0:
        time.sleep(1)

    fitter = TPUFitter(model=net, device=device, config=TrainGlobalConfig)
    fitter.fit(train_loader, validation_loader,test_loader)
    # fitter.run_tuning_and_inference(test_loader, validation_tune_loader)
    # fitter.save(f'model.bin')
    #fitter.run_inference(test_loader)

_mp_fn(rank=1)

#
# file = open('log.txt', 'r')
# for line in file.readlines():
#     print(line[:-1])
# file.close()