import sqlite3
from database import get_db_connection, get_ist_now

def get_overview_stats(tenant_id=1):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        
        # Today stats (same calendar day as log_message / daily_stats — uses configured TIMEZONE)
        today = get_ist_now().strftime("%Y-%m-%d")
        cur.execute(
            "SELECT total_sent, total_delivered, total_read, total_replied, total_blocked FROM daily_stats WHERE tenant_id=? AND date=?",
            (tenant_id, today),
        )
        today_row = cur.fetchone()
        
        total_sent = today_row['total_sent'] if today_row else 0
        total_delivered = today_row['total_delivered'] if today_row else 0
        total_read = today_row['total_read'] if today_row else 0
        total_replied = today_row['total_replied'] if today_row else 0
        blocked_count = today_row['total_blocked'] if today_row else 0

        # Calculate Rates
        delivery_rate = 0 if total_sent == 0 else round((total_delivered / total_sent) * 100, 1)
        read_rate = 0 if total_delivered == 0 else round((total_read / total_delivered) * 100, 1)
        reply_rate = 0 if total_sent == 0 else round((total_replied / total_sent) * 100, 1)

        return {
            "total_sent": total_sent,
            "delivery_rate": delivery_rate,
            "read_rate": read_rate,
            "reply_rate": reply_rate,
            "blocked_count": blocked_count
        }
    finally:
        conn.close()

def get_chart_data(tenant_id=1):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        
        # Bar chart - last 7 days sent
        cur.execute('''
            SELECT date, total_sent FROM daily_stats 
            WHERE tenant_id = ?
            ORDER BY date DESC LIMIT 7
        ''', (tenant_id,))
        bar_rows = cur.fetchall()
        
        # Reverse to show chronological
        bar_labels = [row['date'] for row in reversed(bar_rows)]
        bar_data = [row['total_sent'] for row in reversed(bar_rows)]
        
        # Pie chart - overall sent, delivered, read, replied
        cur.execute('''
            SELECT SUM(total_sent) as s, SUM(total_delivered) as d, 
                   SUM(total_read) as r, SUM(total_replied) as rp 
            FROM daily_stats WHERE tenant_id = ?
        ''', (tenant_id,))
        pie_row = cur.fetchone()
        
        pie_data = [
            pie_row['s'] or 0,
            pie_row['d'] or 0,
            pie_row['r'] or 0,
            pie_row['rp'] or 0
        ]
        
        # Line chart - best time (replies by hour)
        cur.execute('''
            SELECT strftime('%H', timestamp) as hour, COUNT(*) as count 
            FROM messages WHERE direction='received' AND tenant_id = ?
            GROUP BY hour ORDER BY hour ASC
        ''', (tenant_id,))
        hour_rows = cur.fetchall()
        
        hours_dict = {str(i).zfill(2): 0 for i in range(24)}
        for row in hour_rows:
            if row['hour']:
                hours_dict[row['hour']] = row['count']
                
        line_labels = list(hours_dict.keys())
        line_data = list(hours_dict.values())
        
        return {
            "bar": {"labels": bar_labels, "data": bar_data},
            "pie": {"labels": ["Sent", "Delivered", "Read", "Replied"], "data": pie_data},
            "line": {"labels": line_labels, "data": line_data}
        }
    finally:
        conn.close()

def get_all_contacts(tenant_id=1):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute('''
            SELECT name, phone, last_seen, is_blocked, COALESCE(opted_out, 0) as opted_out,
                   total_replies, total_messages_sent,
                   (CASE WHEN is_blocked = 1 THEN 'Blocked' WHEN opted_out = 1 THEN 'Unsubscribed' ELSE 'Active' END) as status
            FROM contacts WHERE tenant_id = ? ORDER BY last_seen DESC
        ''', (tenant_id,))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()

def get_campaigns(tenant_id=1):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute('''
            SELECT
                cr.id,
                cr.campaign_name,
                DATE(cr.created_at) as date,
                cr.status,
                cr.total_contacts,
                cr.sent_count as sent,
                cr.failed_count,
                COALESCE(SUM(CASE WHEN m.status IN ('delivered', 'read') THEN 1 ELSE 0 END), 0) as delivered,
                COALESCE(SUM(CASE WHEN m.status='read' THEN 1 ELSE 0 END), 0) as read
            FROM campaign_runs cr
            LEFT JOIN messages m
                ON m.campaign_run_id = cr.id
               AND m.direction='sent'
               AND m.tenant_id = cr.tenant_id
            WHERE cr.tenant_id = ?
            GROUP BY cr.id
            ORDER BY cr.created_at DESC
        ''', (tenant_id,))
        campaigns = [dict(row) for row in cur.fetchall()]
        
        for c in campaigns:
            sent = c['sent'] or 0
            delivered = c['delivered'] or 0
            read = c['read'] or 0
            
            c['read_rate'] = round((read / sent) * 100, 1) if sent > 0 else 0
            
            # For reply rate, map unique repliers who received a message
            cur.execute('''
                SELECT COUNT(DISTINCT m2.phone) as repliers
                FROM messages m1
                JOIN messages m2 ON m1.phone = m2.phone AND m1.tenant_id = m2.tenant_id
                WHERE m1.campaign_run_id = ? AND m1.direction='sent' AND m1.tenant_id = ?
                  AND m2.direction='received' 
                AND m2.timestamp >= m1.timestamp
            ''', (c['id'], tenant_id))
            r_row = cur.fetchone()
            repliers = r_row['repliers'] if r_row else 0
            
            c['reply_rate'] = round((repliers / sent) * 100, 1) if sent > 0 else 0

        cur.execute('''
            SELECT
                NULL as id,
                campaign_name,
                DATE(timestamp) as date,
                'legacy' as status,
                COUNT(id) as total_contacts,
                COUNT(id) as sent,
                0 as failed_count,
                SUM(CASE WHEN status IN ('delivered', 'read') THEN 1 ELSE 0 END) as delivered,
                SUM(CASE WHEN status='read' THEN 1 ELSE 0 END) as read
            FROM messages
            WHERE direction='sent'
              AND campaign_name IS NOT NULL
              AND tenant_id = ?
              AND campaign_run_id IS NULL
            GROUP BY campaign_name, DATE(timestamp)
            ORDER BY DATE(timestamp) DESC
        ''', (tenant_id,))
        legacy_campaigns = [dict(row) for row in cur.fetchall()]

        for c in legacy_campaigns:
            sent = c['sent'] or 0
            read = c['read'] or 0
            c['read_rate'] = round((read / sent) * 100, 1) if sent > 0 else 0
            cur.execute('''
                SELECT COUNT(DISTINCT m2.phone) as repliers
                FROM messages m1
                JOIN messages m2 ON m1.phone = m2.phone AND m1.tenant_id = m2.tenant_id
                WHERE m1.campaign_name = ? AND m1.direction='sent' AND m1.tenant_id = ?
                  AND m1.campaign_run_id IS NULL
                  AND m2.direction='received'
                  AND m2.timestamp >= m1.timestamp
            ''', (c['campaign_name'], tenant_id))
            r_row = cur.fetchone()
            repliers = r_row['repliers'] if r_row else 0
            c['reply_rate'] = round((repliers / sent) * 100, 1) if sent > 0 else 0

        campaigns.extend(legacy_campaigns)
        campaigns.sort(key=lambda row: (row['date'] or '', row.get('id') or 0), reverse=True)
        return campaigns
    finally:
        conn.close()
