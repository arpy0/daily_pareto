# pylint: disable=invalid-name
# pylint: disable=missing-function-docstring
# pylint: disable=missing-module-docstring
# pylint: disable=unspecified-encoding


import datetime
import json
import os
from pprint import pformat

import matplotlib.ticker as mticker
import matplotlib.pyplot as pl

import numpy as np
import requests


def normed(x):
    return x/np.sum(x)

def pareto_compare(a, b) -> bool:
    # return True if a is beat by b
    if a == b:
        return False
    return all(z[0] >= z[1] for z in zip(a, b))

def score_string(scores, category) -> str:
    print(category)
    return ', '.join(['/'.join([''.join(z) for z in zip(map(str, s), category.lower())]) for s in scores])

class Pick():
    def __init__(self, ordinal, reroll=False):
        # fyi, ordinal is the number of days since 0001-01-01. I use it to uniquely identify each day
        self.day = ordinal
        if reroll or not self.load():
            self.start_scores = self.random()
        self.end_scores = self.get_frontier()
        self.save()
        
    def __repr__(self):
        file = self.filename()
        with open(file) as data:
            return pformat(json.load(data))

    def filename(self) -> str:
        return f'picks/{self.day}.txt'

    def random(self):
        # maybe consider using a seeded random base on date somehow.  it gets messy if you have to reroll though
        rng = np.random.default_rng()

        self.puzzle = rng.choice(rng.choice(grouped_puzzles, p=grouped_puzzles_weight))
        scores = None
        while not scores:
            # here we're continue rolling category until we get a good one
            # TODO: don't pick more than 1 of AHW
            # also, probably ban CR.  all kinds of filtering should be considered

            probs = normed(categories_weight[self.puzzle['type'] == 'PRODUCTION'])
            self.category = ''.join(rng.choice(categories, 3, False, p=probs))
            self.category,self.min_flag = ''.join(sorted(self.category[:2])),self.category[2]
            self.flags = ''.join((rng.choice(['', 'O'], p=[0.99, 0.01]), 
                                  rng.choice(['', 'T'], p=[0.9, 0.1]),
                                  rng.choice(['', self.min_flag], p=[0, 1])))

            # gets the scores.  if there is ever a problem with the category, it will return None which forces this function to reroll category
            scores = self.get_frontier()
        return scores

    def load(self) -> bool:
        try:
            file = self.filename()
            with open(file) as data:
                data_json = json.load(data)
                for i in data_json:
                    self.__dict__[i] = data_json[i]
            return True
        except FileNotFoundError:
            return False

    def save(self):
        with open(self.filename(), 'w') as file:
            json.dump(self.__dict__, file, indent=4)

    def get_frontier(self):
        # todo: maybe should be caching the results incase of a category reroll
        url = f'https://zlbb.faendir.com/om/puzzle/{self.puzzle["id"]}/records?includeFrontier=true'
        frontier = requests.get(url).json()
        for solution in frontier:
            if solution['score']['rate'] in (None, 'Infinity'):
                solution['score']['rate'] = np.inf

        if 'O' not in self.flags:
            frontier = tuple(filter(lambda x: x['score']['overlap'] == False, frontier))
        if 'T' in self.flags:
            frontier = tuple(filter(lambda x: x['score']['trackless'] == True, frontier))
        try:
            if self.min_flag in self.flags:
                self.min_score = min(i['score'][catmap[self.min_flag]] for i in frontier)
                frontier = tuple(filter(lambda x: x['score'][catmap[self.min_flag]] == self.min_score, frontier))
        except AttributeError:
            pass

        # pull out the scored metrics for today
        scores = [tuple(f['score'][catmap[c]] for c in self.category) for f in frontier]
        if None in scores[0]:
            # if one of the scores is None, give up. something is wrong with one of the puzzles.  this will trigger a reroll
            return None

        # filter pareto
        scores = sorted(set(scores))
        scores = [a for a in scores if not any(pareto_compare(a, b) for b in scores)]

        return scores

    def get_discord_announcement(self):
        date = datetime.date.fromordinal(self.day)
        scores_names = score_string(self.start_scores, self.category)
        link = self.get_leaderboard_link()
        return f"```Daily Pareto for {date:%B %d, %Y}:\n{self.flags}({self.category}) for {self.puzzle['displayName']}\n{scores_names}```\n{link}\n"

    def get_leaderboard_link(self):
        link = f'https://zlbb.faendir.com/puzzles/{self.puzzle["id"]}/visualizer?visualizerFilter-{self.puzzle["id"]}.showOnlyFrontier=true&visualizerConfig.mode=2D&visualizerConfig.x={self.category[0].lower()}&visualizerConfig.y={self.category[1].lower()}'
        if 'O' not in self.flags:
            link += f'&visualizerFilter-{self.puzzle["id"]}.modifiers.overlap=false'
        if 'T' in self.flags:
            link += f'&visualizerFilter-{self.puzzle["id"]}.modifiers.trackless=true'
        try:
            if self.min_flag in self.flags:
                link += f'&visualizerFilter-{self.puzzle["id"]}.range.{self.min_flag.lower()}.max={self.min_score}'
        except AttributeError:
            pass
        return link

    def make_chart(self, i):
        date = datetime.date.fromordinal(self.day)

        # idk if I'm using this charting library right, but it's working so I'm going with it.
        pl.figure(figsize=(12,5),layout='tight')
        pl.style.use('dark_background')
        pl.title(f"{self.flags}({self.category}) for {self.puzzle['displayName']} {date:%Y-%m-%d} ")
        pl.xlabel(catmap[self.category[0]])
        pl.ylabel(catmap[self.category[1]])
        pl.grid(True, which='both', alpha=.25)
        if True:
            pl.loglog()
            ax = pl.gca()

            # I like more labels on the lines than it gives by default.  but this is a little bit too much sometimes
            ax.xaxis.set_major_formatter(mticker.ScalarFormatter())
            ax.xaxis.set_minor_formatter(mticker.ScalarFormatter())
            ax.yaxis.set_major_formatter(mticker.ScalarFormatter())
            ax.yaxis.set_minor_formatter(mticker.ScalarFormatter())
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)

        # just fyi, I store infinity as 9999. I know python has a representation for infinity, but I just haven't played with it enough to know it
        # still looking for a good way to render them, but filtering them out for now
        start = set(tuple(s) for s in self.start_scores if np.inf not in s)
        end = set(tuple(s) for s in self.end_scores if np.inf not in s)

        dead = start.difference(end)
        keep = start.intersection(end)
        new = end.difference(start)
        if len(dead): pl.plot(*zip(*dead), 'ro', ms=8, fillstyle='none')
        if len(keep): pl.plot(*zip(*keep), 'ro', ms=8)
        if len(new): pl.plot(*zip(*new), 'go', ms=8)

        pl.savefig(f'charts/{date:%Y-%m-%d}.png')
        pl.savefig({0: 'today', 1: 'yesterday'}.get(i, 'chart')+'.png')
        
if __name__ == '__main__':
    reload = False
    if reload or not os.path.isfile('puzzle.json'):
        puzzles = requests.get('https://zlbb.faendir.com/om/puzzles').json()
        with open('puzzles.json', 'w') as file:
            json.dump(puzzles, file)
    else:
        with open('puzzles.json', 'r') as file:
            puzzles = json.load(file)
    
    # TODO: filter out puzzles like stab water
    
    chapter_puzzles = tuple(filter(lambda x: 'CHAPTER' in x['group']['id'], puzzles))
    journal_puzzles = tuple(filter(lambda x: 'JOURNAL' in x['group']['id'], puzzles))
    tournament_puzzles = tuple(filter(lambda x: 'TOURNAMENT' in x['group']['id'], puzzles))
    
    grouped_puzzles = np.array([chapter_puzzles, journal_puzzles, tournament_puzzles], dtype=object)
    grouped_puzzles_weight = normed([3, 2, 1])
    
    # I'd kind of like to do this in a better datastructure so the names/weights are more closely tied together
    categories = ['G', 'C', 'A', 'I', 'R', 'H', 'W']
    categories_weight = [[2, 2, 2, 2, 1, 1, 1], [2, 2, 2, 2, 1, 0, 0]]  # disable AHW in production.  probably needs a cleaner way
    catmap = {'G': 'cost', 'C': 'cycles', 'A': 'area', 'I': 'instructions', 'R': 'rate', 'H': 'height', 'W': 'width'}

    for i in [0,1]:
        ordinal = datetime.date.today().toordinal() - i 
        pick = Pick(ordinal)
        print(pick)
        print(pick.get_discord_announcement())
        pick.make_chart(i)
        print()
