import logging
from abc import ABC
from threading import Thread, Event

import socketio
from requests import RequestException

from config import config


def get_path(post): return f'>>>/{post["board"]}/{post["thread"] or post["postId"]} ({post["postId"]})'


class Watcher(ABC, Thread):
    def __init__(self):
        Thread.__init__(self)
        self.daemon = True
        self._stp = Event()  # ._stop is reserved

    def stop(self):
        logging.debug(f'Killing thread, goodbye')
        self._stp.set()


class LivePostsWatcher(Watcher):
    def __init__(self, session, notify, evaluate):
        super().__init__()

        client = socketio.Client(  # cannot use class variable to make the annotations
            http_session=session,
            reconnection_delay=config.LIVE_POSTS_RECONNECTION_DELAY,
            reconnection_delay_max=config.LIVE_POSTS_RECONNECTION_DELAY
        )

        @client.event
        def connect():
            logging.debug(f'Live posts client connected')
            client.emit('room', 'globalmanage-recent-hashed')
            notify(f'Connected', f'Watching live posts')

        @client.event
        def disconnect():
            logging.error(f'Live posts client disconnected')
            notify(f'Lost live posts connection', f'Retrying in {config.LIVE_POSTS_RECONNECTION_DELAY} seconds')

        @client.on('newPost')
        def on_new_post(post):  # specifies new post handler
            urls, entries = evaluate(post["nomarkup"])
            if urls or entries:
                notify(f'Alert! {get_path(post)}', '\n'.join(urls) + '\n'.join(entries))

        self.client = client
        self.start()

    def run(self):
        self.client.connect(f'wss://{config.DOMAIN_NAME}/')
        self.client.wait()  # blocks the thread until something happens

        if self._stp.wait():
            logging.info("Exiting live posts watcher")
            self.client.disconnect()


class ReportsWatcher(Watcher):
    def __init__(self, session, notify):
        super().__init__()
        self.session = session

        self.notify = notify
        self.known_reports = 0

        self.start()

    def fetch_reports(self):
        reply = self.session.get(
            url=f'https://{config.DOMAIN_NAME}/globalmanage/reports.json')
        reports = reply.json()["reports"]
        return reports, len(reports)

    def run(self):
        while True:  # main loop, do while bootleg
            try:
                reported_posts, num_reported_posts = self.fetch_reports()
                if 0 < num_reported_posts != self.known_reports:
                    self.notify(f'New reports!',
                                "\n".join([f'{get_path(p)}  {[r["reason"] for r in p["globalreports"]]}' for p in
                                           reported_posts]))
                self.known_reports = num_reported_posts
            except RequestException as e:
                logging.error(f'Exception {e} occurred while fetching reports')
                self.notify(f'Error while fetching reports', f'Trying to reconnect')

            if self._stp.wait(config.FETCH_REPORTS_INTERVAL):
                logging.info("Exiting reports watcher")
                break
