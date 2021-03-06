# -*- coding: utf-8 -*-

# Define here the models for your spider middleware
#
# See documentation in:
# http://doc.scrapy.org/en/latest/topics/spider-middleware.html

from scrapy import signals
from scrapy.spiders import Spider
from fake_useragent import UserAgent
from twisted.web.client import ResponseNeverReceived
from twisted.internet.error import TimeoutError, ConnectionRefusedError, ConnectError
from scrapy.core.downloader.handlers.http11 import TunnelError
from datetime import datetime, timedelta
import requests
import json


# 随机更换User-Agent
class MarketcrawlUserAgentMiddleware(object):

    def __init__(self):
        super(MarketcrawlUserAgentMiddleware, self).__init__()
        self.ua = UserAgent(use_cache_server=False)

    def process_request(self, request, spider):
        assert isinstance(spider, Spider)
        ua = getattr(self.ua, spider.settings['USER_AGETN_TYPE'])
        if ua:
            request.headers.setdefault('User-Agent', ua)
        else:
            spider.logger.info("unkown user agent type by spider: %s" % spider)


class MarketcrawlSpiderMiddleware(object):
    # Not all methods need to be defined. If a method is not defined,
    # scrapy acts as if the spider middleware does not modify the
    # passed objects.

    @classmethod
    def from_crawler(cls, crawler):
        # This method is used by Scrapy to create your spiders.
        s = cls()
        crawler.signals.connect(s.spider_opened, signal=signals.spider_opened)
        return s

    def process_spider_input(self, response, spider):
        # Called for each response that goes through the spider
        # middleware and into the spider.

        # Should return None or raise an exception.
        return None

    def process_spider_output(self, response, result, spider):
        # Called with the results returned from the Spider, after
        # it has processed the response.

        # Must return an iterable of Request, dict or Item objects.
        for i in result:
            yield i

    def process_spider_exception(self, response, exception, spider):
        # Called when a spider or process_spider_input() method
        # (from other spider middleware) raises an exception.

        # Should return either None or an iterable of Response, dict
        # or Item objects.
        pass

    def process_start_requests(self, start_requests, spider):
        # Called with the start requests of the spider, and works
        # similarly to the process_spider_output() method, except
        # that it doesn’t have a response associated.

        # Must return only requests (not items).
        for r in start_requests:
            yield r

    def spider_opened(self, spider):
        spider.logger.info('Spider opened: %s' % spider.name)


class MarketcrawlHttpProxyMiddleware(object):
    # Not all methods need to be defined. If a method is not defined,
    # scrapy acts as if the downloader middleware does not modify the
    # passed objects.

    # 遇到这些类型的错误直接当做代理不可用处理掉, 不再传给retrymiddleware
    DONT_RETRY_ERRORS = (
        TimeoutError,
        ConnectionRefusedError,
        ResponseNeverReceived,
        ConnectError,
        TunnelError,
        ValueError
    )

    def __init__(self, interval, size, threshold):
        # 保存上次不用代理直接连接的时间点
        self.last_no_proxy_time = datetime.now()
        # 初始化代理列表, 第一元素表示直连
        self.proxyes = [{"proxy": None, "ip": None, "valid": True, "count": 0}]
        # 初始时使用0号代理(即无代理,直接连接)
        self.proxy_index = 0
        # 表示可信代理的数量(如自己搭建的HTTP代理)+1(不用代理直接连接)
        self.fixed_proxy = len(self.proxyes)
        # 上一次获取新代理的时间
        self.last_fetch_proxy_time = datetime.now()
        # 一个proxy如果没用到这个数字就被发现老是超时, 则永久移除该proxy. 设为0则不会修改代理文件.
        self.dump_count_threshold = 20
        # 一个将被设为invalid的代理如果已经成功爬取大于这个参数的页面， 将不会被invalid
        self.invalid_proxy_threshold = 200
        # 是否在超时的情况下禁用代理
        self.invalid_proxy_flag = True

        self.recover_interval = interval
        self.fetch_new_proxy_size = size
        self.extend_proxy_threshold = threshold

    @classmethod
    def from_crawler(cls, crawler):
        # This method is used by Scrapy to create your spiders.
        interval = crawler.settings.getint('RECOVER_INTERVAL')
        size = crawler.settings.getint('FETCH_PROXY_FIXED_SIZE')
        threshold = crawler.settings.getint('EXTEND_PROXY_THRESHOLD')

        s = cls(interval, size, threshold)
        crawler.signals.connect(s.spider_opened, signal=signals.spider_opened)
        return s

    # 从本地代理服务中获取若干新的代理IP
    def fetch_proxy(self, spider, protocol=1, count=1):
        r = requests.get('{}/?&count={}&protocol={}'.format(spider.settings['HTTPS_PROXY'], count, protocol))
        ip_ports = json.loads(r.text)
        spider.logger.info("new proxyes: %s, protocol: %s, size: %s" % (ip_ports, protocol, count))

        items = []
        for ip_port in ip_ports:
            ip = ip_port[0]
            port = ip_port[1]

            item = {
                'ip': ip,
                'port': port
            }
            items.append(item)
        return items

    # 在本地代理服务中删除指定的代理IP
    def remove_proxy(self, spider, p):
        if p is not None:
            r = requests.get('{}/delete?ip={}'.format(spider.settings['HTTPS_PROXY'], p['ip']))
            spider.logger.info("remove invalid proxy: %s, result: %s", p, r.text)
        else:
            pass

    # 返回一个代理url是否在代理列表中
    def url_in_proxyes(self, url):
        for p in self.proxyes:
            if url == p["proxy"]:
                return True
        return False

    # 将所有count>=指定阈值的代理重置为valid
    def reset_proxyes(self):
        for p in self.proxyes:
            if p["count"] >= self.dump_count_threshold:
                p["valid"] = True

    # 返回proxy列表中有效的代理数量
    def len_valid_proxy(self):
        count = 0
        for p in self.proxyes:
            if p["valid"]:
                count += 1
        return count

    def fetch_new_proxyes(self, spider):
        assert isinstance(spider, Spider)
        for i in range(1, 3):
            new_proxyes = self.fetch_proxy(spider, i, self.fetch_new_proxy_size)
            self.last_fetch_proxy_time = datetime.now()

            for np in new_proxyes:
                if self.url_in_proxyes(np):
                    continue
                else:
                    self.proxyes.append({"proxy": 'https://%s:%s' % (np['ip'], np['port']),
                                         "ip": np['ip'],
                                         "valid": True,
                                         "count": 0})

        # 如果发现抓不到什么新的代理了, 缩小threshold以避免白费功夫
        if self.len_valid_proxy() < self.extend_proxy_threshold:
            self.extend_proxy_threshold -= 1

    def delete_invalid_proxy(self, spider):
        assert isinstance(spider, Spider)
        if self.dump_count_threshold <= 0:
            return

        for i in range(len(self.proxyes)-1, self.fixed_proxy-1, -1):
            p = self.proxyes[i]
            if p["valid"] is False:
                self.remove_proxy(spider, p)
                self.proxyes.pop(i)
            else:
                pass

    def inc_proxy_index(self, spider, current=-1):
        assert isinstance(spider, Spider)
        if current != -1 and self.proxy_index != current:
            return

        # 将代理列表的索引移到下一个有效代理的位置
        while True:
            self.proxy_index = (self.proxy_index + 1) % len(self.proxyes)
            if self.proxyes[self.proxy_index]["valid"]:
                break

        # 两轮proxy_index==0的时间间隔过短， 说明出现了验证码抖动，扩展代理列表
        if self.proxy_index == 0 and datetime.now() < self.last_no_proxy_time + timedelta(minutes=2):
            spider.logger.info("captcha thrashing")
            self.fetch_new_proxyes(spider)

        # 代理列表只有fixed_proxy项有效, 有效的代理不足需重置为valid
        if self.len_valid_proxy() <= self.fixed_proxy or self.len_valid_proxy() < self.extend_proxy_threshold:
            spider.logger.info("reset proxyes to valid")
            self.reset_proxyes()

        # 代理数量仍然不足, 抓取新的代理
        if self.len_valid_proxy() < self.extend_proxy_threshold:
            spider.logger.info("valid proxy < threshold: %d/%d" % (self.len_valid_proxy(), self.extend_proxy_threshold))
            self.fetch_new_proxyes(spider)

        spider.logger.info("now using new proxy: %s" % self.proxyes[self.proxy_index]["proxy"])

    def invalid_proxy(self, index, spider):
        assert isinstance(spider, Spider)

        # 可信代理永远不会设为invalid
        if index < self.fixed_proxy:
            spider.logger.info("fixed proxy will not be invalid: %s" % self.proxyes[index])
            self.inc_proxy_index(spider, index)
            return

        # 将index指向的proxy设置为invalid
        if self.proxyes[index]["valid"]:
            spider.logger.info("invalidate %s" % self.proxyes[index])
            self.proxyes[index]["valid"] = False

            # 并调整当前proxy_index到下一个有效代理的位置
            if index == self.proxy_index:
                self.inc_proxy_index(spider)

            if self.proxyes[index]["count"] < self.dump_count_threshold:
                self.delete_invalid_proxy(spider)

    def set_proxy(self, request, spider):
        proxy = self.proxyes[self.proxy_index]

        # 如果获取的proxy已经失效则顺延到下一个
        if not proxy["valid"]:
            self.inc_proxy_index(spider)
            proxy = self.proxyes[self.proxy_index]

        # 每次不用代理直接下载时更新self.last_no_proxy_time
        if self.proxy_index == 0:
            self.last_no_proxy_time = datetime.now()

        # 如果使用代理，则在request中添加元信息配置，否则删除该元信息
        if proxy["proxy"]:
            request.meta["proxy"] = proxy["proxy"]
        elif "proxy" in request.meta.keys():
            del request.meta["proxy"]

        request.meta["proxy_index"] = self.proxy_index
        proxy["count"] += 1

    def process_request(self, request, spider):
        # Called for each request that goes through the downloader
        # middleware.

        # Must either:
        # - return None: continue processing this request
        # - or return a Response object
        # - or return a Request object
        # - or raise IgnoreRequest: process_exception() methods of
        #   installed downloader middleware will be called

        # 使用代理超过一定时间后，主动切回直接连接,也就是把下标proxy_index重置为0
        if self.proxy_index > 0 and datetime.now() > (self.last_no_proxy_time + timedelta(minutes=self.recover_interval)):
            spider.logger.info("After %d minutes later, recover from using proxy" % self.recover_interval)
            self.last_no_proxy_time = datetime.now()
            self.proxy_index = 0

        # spider发现parse error, 强制更换代理
        if "change_proxy" in request.meta.keys() and request.meta["change_proxy"]:
            spider.logger.info("change proxy request get by spider: %s" % request)
            self.invalid_proxy(request.meta["proxy_index"], spider)
            request.meta["change_proxy"] = False

        # 有些代理会把请求重定向到一个莫名其妙的地址
        request.meta["dont_redirect"] = True

        # Scrapy将继续处理该request，执行其他的中间件的相应方法，直到合适的下载器处理函数(downloadhandler)被调用
        self.set_proxy(request, spider)
        return None

    def process_response(self, request, response, spider):
        # Called with the response returned from the downloader.

        # Must either;
        # - return a Response object
        # - return a Request object
        # - or raise IgnoreRequest
        if "proxy" in request.meta.keys():
            spider.logger.info("%s %s %s" % (request.meta["proxy"], response.status, request.url))
        else:
            spider.logger.info("None %s %s" % (response.status, request.url))

        # status不是正常的200而且不在spider声明的正常爬取过程中可能出现的status列表中, 则认为代理无效, 则强制切换代理
        if response.status != 200:
            if not hasattr(spider, "website_possible_httpstatus_list") \
                    or response.status not in spider.website_possible_httpstatus_list:

                spider.logger.info("response status[%d] not in spider.website_possible_httpstatus_list" % response.status)
                self.invalid_proxy(request.meta["proxy_index"], spider)
                new_request = request.copy()
                new_request.dont_filter = True
                return new_request

            # 其他错误时直接进行重试
            else:
                return request
        else:
            return response

    def process_exception(self, request, exception, spider):
        # Called when a download handler or a process_request()
        # (from other downloader middleware) raises an exception.

        # Must either:
        # - return None: continue processing this exception
        # - return a Response object: stops process_exception() chain
        # - return a Request object: stops process_exception() chain
        spider.logger.debug("%s exception: %s" % (self.proxyes[request.meta["proxy_index"]]["proxy"], exception))
        request_proxy_index = request.meta["proxy_index"]

        # 遇到这些类型的错误直接当做代理不可用处理掉, 不再传给retrymiddleware
        if isinstance(exception, self.DONT_RETRY_ERRORS):
            if request_proxy_index > self.fixed_proxy - 1 and self.invalid_proxy_flag:  # WARNING 直连时超时的话换个代理还是重试? 这是策略问题
                if self.proxyes[request_proxy_index]["count"] < self.invalid_proxy_threshold:
                    self.invalid_proxy(request_proxy_index, spider)
                elif request_proxy_index == self.proxy_index:  # 虽然超时，但是如果之前一直很好用，也不设为invalid
                    self.inc_proxy_index(spider)
            else:  # 简单的切换而不禁用
                if request.meta["proxy_index"] == self.proxy_index:
                    self.inc_proxy_index(spider)
            new_request = request.copy()
            new_request.dont_filter = True
            return new_request

        # 其他异常时不进行处理，直接给retrymiddleware处理
        else:
            return None

    def spider_opened(self, spider):
        pass
