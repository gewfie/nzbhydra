from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import calendar
import logging
from itertools import groupby

import arrow
from builtins import *
from peewee import fn, JOIN

from nzbhydra import database
from nzbhydra.database import Indexer, IndexerApiAccess, IndexerNzbDownload, IndexerSearch, Search, IndexerStatus, TvIdCache, MovieIdCache, SearchResult
from nzbhydra.exceptions import IndexerNotFoundException
from nzbhydra.indexers import getIndexerByName

logger = logging.getLogger('root')


def get_indexer_response_times():
    result = []
    for p in Indexer.select().order_by(Indexer.name):
        print("Limiting stats to 100 for testing only!")
        result.append({"key": p.name,
                       "values": [{"responseTime": x.response_time, "date": x.time.timestamp} for x in IndexerApiAccess().select(IndexerApiAccess.response_time, IndexerApiAccess.time).where((IndexerApiAccess.response_successful) & (IndexerApiAccess.indexer == p)).join(Indexer).limit(1)]})
    return result


def get_avg_indexer_response_times():
    result = []
    response_times = []
    for p in Indexer.select().order_by(Indexer.name):
        try:
            indexer = getIndexerByName(p.name)
            if not indexer.settings.enabled:
                logger.debug("Skipping download stats for %s" % p.name)
                continue
        except IndexerNotFoundException:
            logger.error("Unable to find indexer %s in configuration" % p.name)
            continue
        avg_response_time = IndexerApiAccess().select(fn.AVG(IndexerApiAccess.response_time)).where((IndexerApiAccess.response_successful) & (IndexerApiAccess.indexer == p)).tuples()[0][0]
        if avg_response_time:
            response_times.append({"name": p.name, "avgResponseTime": int(avg_response_time)})
    avg_response_time = IndexerApiAccess().select(fn.AVG(IndexerApiAccess.response_time)).where((IndexerApiAccess.response_successful) & (IndexerApiAccess.response_time is not None)).tuples()[0][0]
    for i in response_times:
        delta = i["avgResponseTime"] - avg_response_time
        i["delta"] = delta
        result.append(i)
    result = sorted(result, key=lambda x: x["name"])
    result = sorted(result, key=lambda x: x["avgResponseTime"])

    return result


def get_avg_indexer_search_results_share():
    results = []
    for p in Indexer.select().order_by(Indexer.name):
        try:
            indexer = getIndexerByName(p.name)
            if not indexer.settings.enabled:
                logger.debug("Skipping download stats for %s" % p.name)
                continue
            if indexer.settings.name == "Womble":
                logger.debug("Skipping download stats for Womble because we exclude update queries without specific query or ID")
                continue
        except IndexerNotFoundException:
            logger.error("Unable to find indexer %s in configuration" % p.name)
            continue
        result = database.db.execute_sql(
            """
            SELECT (100 *
            (SELECT cast(sum(ps.resultsCount) AS FLOAT)
             FROM indexersearch ps
             WHERE ps.search_id IN (SELECT ps.search_id
                                    FROM indexersearch ps, search s
                                    WHERE ps.indexer_id == %d AND ps.search_id = s.id AND ps.successful AND (s.episode NOT NULL OR s.season NOT NULL OR s.identifier_key NOT NULL OR s.query NOT NULL)) AND ps.indexer_id == %d))
           /
           (SELECT sum(ps.resultsCount)
            FROM indexersearch ps
            WHERE ps.search_id IN (SELECT ps.search_id
                                   FROM indexersearch ps, search s
                                   WHERE ps.indexer_id == %d AND ps.search_id = s.id AND ps.successful AND (s.episode NOT NULL OR s.season NOT NULL OR s.identifier_key NOT NULL OR s.query NOT NULL))) AS sumAllResults
             """
            % (p.id, p.id, p.id)).fetchone()
        avgResultsShare = int(result[0]) if result is not None and result[0] is not None else "N/A"

        result = database.db.execute_sql(
            """
            SELECT avg(
                CASE WHEN uniqueResults > 0
                  THEN
                    100 / (processedResults * 1.0 / uniqueResults)
                ELSE 0
                END) as avgUniqueResults
            FROM indexersearch
            WHERE processedResults IS NOT NULL AND uniqueResults IS NOT NULL
                  AND indexer_id == %d
            GROUP BY indexer_id;

            """
            % p.id).fetchone()
        if p.name in ["NZBIndex", "Binsearch", "NZBClub"]:
            avgUniqueResults = "-"
        elif result is not None and result[0] is not None:
            avgUniqueResults = int(result[0])
        else:
            avgUniqueResults = "N/A"
        results.append({"name": p.name, "avgResultsShare": avgResultsShare, "avgUniqueResults": avgUniqueResults})
    results = sorted(results, key=lambda x: x["name"])
    results = sorted(results, key=lambda x: 0 if x["avgResultsShare"] == "N/A" else x["avgResultsShare"], reverse=True)
    return results


def get_avg_indexer_access_success():
    dbResults = database.db.execute_sql(
        """
        SELECT
          query1.name,
          query1.failed,
          query1.success,
          query2.average
        FROM (SELECT
                p.name,
                failed.failed,
                success.success,
                p.id AS indexer_id

              FROM indexer p LEFT OUTER JOIN (SELECT
                                                count(1)     AS failed,
                                                p.indexer_id AS pid1
                                              FROM indexerapiaccess p
                                              WHERE NOT p.response_successful
                                              GROUP BY p.indexer_id) AS failed ON p.id == failed.pid1
                LEFT OUTER JOIN (SELECT
                                   count(1)     AS success,
                                   p.indexer_id AS pid2
                                 FROM indexerapiaccess p
                                 WHERE p.response_successful
                                 GROUP BY p.indexer_id) AS success
                  ON success.pid2 = p.id) query1,

          (SELECT
             round(avg(u.sum)) AS average,
             indexer.name,
             indexer.id        AS indexer_id
           FROM
             (SELECT
                t.date,
                t.sum,
                t.indexer_id
              FROM
                (SELECT
                   count(*)     AS sum,
                   date(x.time) AS date,
                   x.indexer_id AS indexer_id
                 FROM
                   indexerapiaccess x
                 GROUP BY
                   date(x.time),
                   x.indexer_id
                ) t
              WHERE t.indexer_id != 0) u
             LEFT JOIN indexer ON u.indexer_id = indexer.id
           GROUP BY u.indexer_id) query2

        WHERE query1.indexer_id == query2.indexer_id
        """).fetchall()
    results = []
    for i in dbResults:
        name = i[0]
        try:
            indexer = getIndexerByName(name)
            if not indexer.settings.enabled:
                logger.debug("Skipping download stats for %s" % name)
                continue
        except IndexerNotFoundException:
            logger.error("Unable to find indexer %s in configuration" % name)
            continue
        failed = i[1] if i[1] is not None else 0
        success = i[2] if i[2] is not None else 0
        averagePerDay = i[3]
        sumall = failed + success
        failedPercent = (100 * failed) / sumall if sumall > 0 else "N/A"
        successPercent = (100 * success) / sumall if sumall > 0 else "N/A"
        results.append({"name": name, "failed": failed, "success": success, "failedPercent": failedPercent, "successPercent": successPercent, "averagePerDay": averagePerDay})
    results = sorted(results, key=lambda x: x["name"])
    results = sorted(results, key=lambda x: x["averagePerDay"], reverse=True)
    return results


def getTimeBasedDownloadStats():
    downloads = IndexerNzbDownload(). \
        select(Indexer.name, IndexerApiAccess.response_successful, IndexerNzbDownload.time). \
        join(IndexerApiAccess, JOIN.LEFT_OUTER). \
        join(Indexer, JOIN.LEFT_OUTER)
    downloadTimes = [arrow.get(x.time) for x in downloads]

    perDayOfWeek, perHourOfDay = calculcateTimeBasedStats(downloadTimes)

    return {"perDayOfWeek": perDayOfWeek, "perHourOfDay": perHourOfDay}


def getTimeBasedSearchStats():
    searches = Search().select(Search.time)
    searchTimes = [arrow.get(x.time) for x in searches]

    perDayOfWeek, perHourOfDay = calculcateTimeBasedStats(searchTimes)

    return {"perDayOfWeek": perDayOfWeek, "perHourOfDay": perHourOfDay}


def calculcateTimeBasedStats(downloadTimes):
    daysOfWeek = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    perDayOfWeek = {x: 0 for x in range(1, 8)}  # Even days withot activity should be contained so they're included in the graph
    for x in range(1, 8):
        dayKey = lambda x: x.isoweekday()
        sortedByDay = sorted(downloadTimes, key=dayKey)
        for key, group in groupby(sortedByDay, dayKey):
            perDayOfWeek[key] = len(list(group))
    perDayOfWeek = [{"day": daysOfWeek[key - 1], "count": value} for key, value in perDayOfWeek.iteritems()]
    perHourOfDay = {x: 0 for x in range(0, 24)}  # See above
    for x in range(1, 8):
        hourKey = lambda x: x.hour
        sortedByHour = sorted(downloadTimes, key=hourKey)
        for key, group in groupby(sortedByHour, hourKey):
            perHourOfDay[key] = len(list(group))
    perHourOfDay = [{"hour": key, "count": value} for key, value in perHourOfDay.iteritems()]
    return perDayOfWeek, perHourOfDay


def getIndexerBasedDownloadStats():
    enabledIndexerIds = []
    for p in Indexer.select().order_by(Indexer.name):
        try:
            indexer = getIndexerByName(p.name)
            if not indexer.settings.enabled:
                logger.debug("Skipping download stats for %s because it's disabled" % p.name)
                continue
            enabledIndexerIds.append(str(p.id))
        except IndexerNotFoundException:
            logger.error("Unable to find indexer %s in configuration" % p.name)
            continue
    enabledIndexerIds = ", ".join(enabledIndexerIds)
    query = """
    SELECT
      indexer.name,
      count(*) AS total,
      CASE WHEN count(*) > 0
        THEN
          100 / (1.0 * countall.countall / count(*))
      ELSE 0
      END
               AS share
    FROM
      indexernzbdownload dl,
      (SELECT count(*) AS countall
       FROM
         indexernzbdownload dl
         LEFT OUTER JOIN indexerapiaccess api
           ON dl.apiAccess_id = api.id
       WHERE api.indexer_id IN (%s))
      countall
      LEFT OUTER JOIN indexerapiaccess api
        ON dl.apiAccess_id = api.id
      LEFT OUTER JOIN indexer indexer
        ON api.indexer_id = indexer.id
    WHERE api.indexer_id IN (%s)
    GROUP BY indexer.id
    """ % (enabledIndexerIds, enabledIndexerIds)
    stats = database.db.execute_sql(query).fetchall()
    stats = [{"name": x[0], "total": x[1], "share": x[2]} for x in stats]

    stats = sorted(stats, key=lambda x: x["name"])
    stats = sorted(stats, key=lambda x: x["share"], reverse=True)
    return stats


def get_nzb_downloads(page=0, limit=100, type=None):
    query = IndexerNzbDownload() \
        .select(Indexer.name.alias("indexerName"), IndexerNzbDownload.title, IndexerNzbDownload.time, IndexerNzbDownload.internal, SearchResult.id.alias('searchResultId'), SearchResult.details.alias('detailsLink'), IndexerApiAccess.response_successful, IndexerApiAccess.username) \
        .switch(IndexerNzbDownload).join(IndexerApiAccess, JOIN.LEFT_OUTER).join(Indexer, JOIN.LEFT_OUTER) \
        .switch(IndexerNzbDownload).join(SearchResult, JOIN.LEFT_OUTER)

    if type == "Internal":
        query = query.where(IndexerNzbDownload.internal)
    elif type == "API":
        query = query.where(~IndexerNzbDownload.internal)

    total_downloads = query.count()
    nzb_downloads = list(query.order_by(IndexerNzbDownload.time.desc()).paginate(page, limit).dicts())
    downloads = {"totalDownloads": total_downloads, "nzbDownloads": nzb_downloads}
    return downloads


# ((Search.identifier_value == MovieIdCache.imdb) & (Search.identifier_key == "imdbid"))
def get_search_requests(page=0, limit=100, type=None):
    query = Search().select(Search.time, Search.internal, Search.query, Search.identifier_key, Search.identifier_value, Search.category, Search.season, Search.episode, Search.type, Search.username, Search.title, Search.author, TvIdCache.title.alias("tvtitle"), MovieIdCache.title.alias("movietitle")).join(TvIdCache,
                                                                                                                                                                                                                                                                                     JOIN.LEFT_OUTER, on=(
            ((Search.identifier_value == TvIdCache.tvdb) & (Search.identifier_key == "tvdbid")) |
            ((Search.identifier_value == TvIdCache.tvrage) & (Search.identifier_key == "rid"))
        )).join(MovieIdCache, JOIN.LEFT_OUTER, on=(
        ((Search.identifier_value == MovieIdCache.imdb) & (Search.identifier_key == "imdbid")) |
        ((Search.identifier_value == MovieIdCache.tmdb) & (Search.identifier_key == "tmdbid"))))

    if type is not None and type != "All":
        query = query.where(Search.internal) if type == "Internal" else query.where(~Search.internal)
    total_requests = query.count()
    requests = list(query.order_by(Search.time.desc()).paginate(page, limit).dicts())

    search_requests = {"totalRequests": total_requests, "searchRequests": requests}
    return search_requests


def get_indexer_statuses():
    return list(IndexerStatus().select(Indexer.name, IndexerStatus.first_failure, IndexerStatus.latest_failure, IndexerStatus.disabled_until, IndexerStatus.level, IndexerStatus.reason).join(Indexer).dicts())
