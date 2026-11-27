import importlib.util
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).parents[2]
spec=importlib.util.spec_from_file_location('source_parser',ROOT/'finance-market-research/scripts/check_sources.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class EmbeddedDateTests(unittest.TestCase):
    def test_hydration_retains_article_identity_and_timezone(self):
        article={'@type':'NewsArticle','url':'https://example.com/story','headline':'政策公告','datePublished':'2026-11-27T04:22:24.000Z','dateModified':'2026-11-27T04:25:06Z'}
        encoded=json.dumps({'props':{'__html':json.dumps(article,ensure_ascii=False)}},ensure_ascii=False)
        html='<title>政策公告</title><script>self.__next_f.push([1,'+json.dumps(encoded)+'])</script><p>正文</p>'
        parsed=m.inspect(html.encode(),'text/html; charset=utf-8','https://example.com/story')
        rows=parsed['structured_jsonld'];self.assertEqual(1,len(rows));self.assertEqual(article,rows[0])
        self.assertNotIn('2026-11-27',parsed['visible_text'])

    def test_malformed_or_untyped_dates_are_not_promoted(self):
        html='<script>{"datePublished":"2026-11-27T04:22:24Z"}; alert("run")</script><script>{broken</script>'
        self.assertEqual([],m.embedded_articles(html))
        for malformed in [None, {'bad':'type'}, [{'nested':'type'}]]:
            html='<script>'+json.dumps({'@type':malformed,'datePublished':'2026-11-27T04:22:24Z'})+'</script>'
            self.assertEqual([],m.embedded_articles(html))


if __name__=='__main__':unittest.main()
