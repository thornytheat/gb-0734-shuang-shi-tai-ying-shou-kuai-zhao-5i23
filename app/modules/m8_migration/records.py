"""账龄迁移记录：回指期初版本、补报版本与期末凭证，原桶位保留在案。"""

DUE_BASIS = {
    "opening": "期初知悉截止可见版本的到期日，保持原结账桶位",
    "restated": "期末知悉下载至期初有效日的版本到期日，迟报重定原桶位",
    "migration": "回补后余额按期末可见版本到期日迁移；已退出发票按回补后到期日账龄化至期末",
    "closing": "期末知悉截止可见版本的到期日，含延期更正",
}

CAUSES = ("DUE_CORRECTED", "LATE_REVISION", "LATE_ARRIVAL", "AGED", "ENTERED", "EXITED", "CLEARED")


def migration_records(book):
    records = []
    for row in book["bridge"]["invoices"]:
        opening, restated, closing = row["opening"], row["restated"], row["closing"]
        path = row["bucket_path"]
        due = {"opening": opening["due"] if opening else None,
               "restated": restated["due"] if restated else None,
               "closing": closing["due"] if closing else None}
        due_corrected = len({value for value in due.values() if value}) > 1
        moved = len({path["opening"], path["restated"], path["aged_to"], path["closing"]}) > 1
        if not (due_corrected or moved):
            continue
        causes = []
        if due_corrected:
            causes.append("DUE_CORRECTED")
        if opening and restated and restated["version"]["event_id"] != opening["version"]["event_id"]:
            causes.append("LATE_REVISION")
        if opening is None and restated is not None:
            causes.append("LATE_ARRIVAL")
        if path["restated"] is not None and path["restated"] != path["aged_to"]:
            causes.append("AGED")
        if restated is None and closing is not None:
            causes.append("ENTERED")
        if restated is not None and closing is None:
            causes.append("EXITED")
        prior = restated or opening
        if closing is not None and closing["balance_cents"] == 0 and prior and prior["balance_cents"] != 0:
            causes.append("CLEARED")
        records.append({
            "invoice_id": row["invoice_id"], "customer_id": row["customer_id"],
            "causes": causes, "due": due, "bucket_path": path,
            "balance_cents": {"opening": opening["balance_cents"] if opening else None,
                              "restated": restated["balance_cents"] if restated else None,
                              "closing": closing["balance_cents"] if closing else None},
            "opening_version": opening["version"] if opening else None,
            "restated_version": restated["version"] if restated else None,
            "closing_version": closing["version"] if closing else None,
            "vouchers": [move["event"] for move in closing["movements"]] if closing else [],
        })
    return records


def unexplained(bridge):
    buckets = [{"bucket": row["bucket"], "label": row["label"], "difference_cents": row["difference_cents"]}
               for row in bridge["buckets"] if row["difference_cents"] != 0]
    amount = (abs(bridge["totals"]["difference_cents"]) + abs(bridge["migration_net_cents"])
              + sum(abs(row["difference_cents"]) for row in buckets))
    return {"totals_cents": bridge["totals"]["difference_cents"],
            "migration_net_cents": bridge["migration_net_cents"],
            "buckets": buckets, "amount_cents": amount}
