import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import bot
import server_stats


class ServerStatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = bot.DB
        bot.DB = Path(self.tmp.name) / "stats-test.db"
        bot.setup_db()

    def tearDown(self):
        bot.DB = self.original_db
        self.tmp.cleanup()

    def test_counts_only_current_human_members(self):
        gid = 123
        human = SimpleNamespace(id=1,bot=False,roles=[SimpleNamespace(name='Paragon')])
        other = SimpleNamespace(id=2,bot=False,roles=[])
        robot = SimpleNamespace(id=3,bot=True,roles=[SimpleNamespace(name='Paragon')])
        guild = SimpleNamespace(id=gid,members=[human,other,robot])
        with bot.connect() as db:
            db.executemany("INSERT INTO members(guild_id,user_id,xp) VALUES (?,?,?)",
                           [(gid,1,5000),(gid,2,300),(gid,3,1000),(gid,4,10000),(999,1,200)])
        self.assertEqual(server_stats.counts(bot,guild),{'members':2,'xp':5300,'paragon':1})
        self.assertEqual(server_stats.name('xp',5300),'Total XP: 5,300')

    def test_configuration_persists(self):
        config={'enabled':True,'category':123,'channels':{'members':456}}
        server_stats.save(bot,config)
        self.assertEqual(server_stats.load(bot),config)
