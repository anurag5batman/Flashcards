from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, abort
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, inspect, text
import json, io, random, re
from datetime import datetime, timedelta
import os
from datetime import datetime, timedelta
from flask import request, jsonify

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"), static_folder=os.path.join(BASE_DIR, "static"))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'flashcards.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Models (Flashcard and Review)
class Flashcard(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    question = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)
    subject = db.Column(db.String(120), nullable=True)
    tags = db.Column(db.String(256), nullable=True)
    difficulty = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    reps = db.Column(db.Integer, default=0)
    easiness = db.Column(db.Float, default=2.5)
    interval = db.Column(db.Integer, default=0)
    next_review = db.Column(db.DateTime, nullable=True)

    def to_dict(self, include_meta=True):
        out = {
            "id": self.id, "question": self.question, "answer": self.answer,
            "subject": self.subject, "tags": self.tags, "difficulty": self.difficulty,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }
        if include_meta:
            out.update({
                "reps": self.reps, "easiness": float(self.easiness) if self.easiness is not None else None,
                "interval": self.interval, "next_review": self.next_review.isoformat() if self.next_review else None
            })
        return out

class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    card_id = db.Column(db.Integer, db.ForeignKey('flashcard.id'), nullable=False)
    when = db.Column(db.DateTime, default=datetime.utcnow)
    quality = db.Column(db.Integer, nullable=False)
    prior_reps = db.Column(db.Integer, nullable=True)
    prior_interval = db.Column(db.Integer, nullable=True)
    prior_easiness = db.Column(db.Float, nullable=True)
    new_reps = db.Column(db.Integer, nullable=True)
    new_interval = db.Column(db.Integer, nullable=True)
    new_easiness = db.Column(db.Float, nullable=True)

# DB init & lightweight migration
with app.app_context():
    db.create_all()
    insp = inspect(db.engine)
    cols = {c['name'] for c in insp.get_columns('flashcard')}
    alter_cmds = []
    if 'reps' not in cols:
        alter_cmds.append("ALTER TABLE flashcard ADD COLUMN reps INTEGER DEFAULT 0")
    if 'easiness' not in cols:
        alter_cmds.append("ALTER TABLE flashcard ADD COLUMN easiness FLOAT DEFAULT 2.5")
    if 'interval' not in cols:
        alter_cmds.append("ALTER TABLE flashcard ADD COLUMN interval INTEGER DEFAULT 0")
    if 'next_review' not in cols:
        alter_cmds.append("ALTER TABLE flashcard ADD COLUMN next_review DATETIME")
    for c in alter_cmds:
        try:
            db.session.execute(text(c)); db.session.commit()
        except Exception:
            db.session.rollback()
    for card in Flashcard.query.all():
        changed = False
        if card.reps is None: card.reps = 0; changed = True
        if card.easiness is None: card.easiness = 2.5; changed = True
        if card.interval is None: card.interval = 0; changed = True
        if changed: db.session.add(card)
    db.session.commit()

# SM-2 helper (unchanged)
def sm2_update(card, quality):
    prior = {"reps": card.reps, "interval": card.interval, "easiness": float(card.easiness or 2.5)}
    if quality < 3:
        card.reps = 0; card.interval = 1
    else:
        card.reps = (card.reps or 0) + 1
        if card.reps == 1: card.interval = 1
        elif card.reps == 2: card.interval = 6
        else: card.interval = max(1, round((card.interval or 1) * (card.easiness or 2.5)))
    q = quality; e = card.easiness or 2.5
    e_prime = e + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    if e_prime < 1.3: e_prime = 1.3
    card.easiness = e_prime
    card.next_review = datetime.utcnow() + timedelta(days=card.interval or 0)
    new = {"reps": card.reps, "interval": card.interval, "easiness": float(card.easiness)}
    return prior, new

@app.context_processor
def inject_counts():
    total = Flashcard.query.count()
    return dict(total_cards=total)

# ---------- Routes ----------

# Root -> Create flashcard page (unchanged)
@app.route('/')
def create_page():
    return render_template('create.html')

@app.route('/add', methods=['POST'])
def add_card():
    q = request.form.get('question','').strip()
    a = request.form.get('answer','').strip()
    if not q or not a:
        abort(400, "question and answer required")
    subject = request.form.get('subject','').strip() or None
    tags = request.form.get('tags','').strip() or None
    difficulty = request.form.get('difficulty','').strip() or None
    card = Flashcard(question=q, answer=a, subject=subject, tags=tags, difficulty=difficulty)
    card.next_review = None
    db.session.add(card); db.session.commit()
    return redirect(url_for('create_page'))

# --- Dashboard: list recent cards (default 20) ---
@app.route('/dashboard')
def dashboard():
    try:
        limit = int(request.args.get('limit', 20))
    except (TypeError, ValueError):
        limit = 20
    cards = Flashcard.query.order_by(Flashcard.created_at.desc()).limit(limit).all()
    cards_d = [c.to_dict() for c in cards]
    for c in cards_d:
        c['next_review'] = c['next_review'][:19] if c['next_review'] else None
    return render_template('dashboard.html', cards=cards_d, limit=limit)

# delete from dashboard
@app.route('/delete/<int:card_id>', methods=['POST'])
def delete_card(card_id):
    card = Flashcard.query.get_or_404(card_id)
    Review.query.filter_by(card_id=card.id).delete()
    db.session.delete(card); db.session.commit()
    # if coming from dashboard, go back there
    return redirect(request.referrer or url_for('dashboard'))

# history, import/export (unchanged)
@app.route('/history/<int:card_id>')
def history_view(card_id):
    card = Flashcard.query.get_or_404(card_id)
    revs = Review.query.filter_by(card_id=card.id).order_by(Review.when.desc()).all()
    revs_d = []
    for r in revs:
        revs_d.append({
            "when": r.when.isoformat()[:19],
            "quality": r.quality,
            "prior_reps": r.prior_reps,
            "prior_interval": r.prior_interval,
            "prior_easiness": float(r.prior_easiness or 2.5),
            "new_reps": r.new_reps,
            "new_interval": r.new_interval,
            "new_easiness": float(r.new_easiness or 2.5)
        })
    cd = card.to_dict(); cd['next_review'] = cd['next_review'][:19] if cd['next_review'] else None
    return render_template('history.html', card=cd, revs=revs_d)

@app.route('/export')
def export_json():
    cards = [c.to_dict(include_meta=True) for c in Flashcard.query.order_by(Flashcard.created_at.desc()).all()]
    reviews = []
    for r in Review.query.order_by(Review.when.asc()).all():
        reviews.append({
            "card_id": r.card_id, "when": r.when.isoformat(), "quality": r.quality,
            "prior_reps": r.prior_reps, "prior_interval": r.prior_interval, "prior_easiness": float(r.prior_easiness or 2.5),
            "new_reps": r.new_reps, "new_interval": r.new_interval, "new_easiness": float(r.new_easiness or 2.5)
        })
    payload = {"cards": cards, "reviews": reviews, "exported_at": datetime.utcnow().isoformat()}
    s = json.dumps(payload, indent=2)
    buf = io.BytesIO(s.encode('utf-8')); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="flashcards_export_full.json", mimetype="application/json")

@app.route('/import', methods=['GET','POST'])
def import_page():
    if request.method == 'POST':
        f = request.files.get('file')
        if not f: return "No file", 400
        try:
            payload = json.load(f)
            cards = payload.get('cards', [])
            reviews = payload.get('reviews', [])
            id_map = {}
            for c in cards:
                q = c.get('question') or ''; a = c.get('answer') or ''
                if not q or not a: continue
                card = Flashcard(question=q, answer=a, subject=c.get('subject'), tags=c.get('tags'), difficulty=c.get('difficulty'))
                card.reps = c.get('reps') or 0; card.easiness = c.get('easiness') or 2.5; card.interval = c.get('interval') or 0
                nr = c.get('next_review')
                if nr:
                    try: card.next_review = datetime.fromisoformat(nr)
                    except: card.next_review = None
                else: card.next_review = None
                db.session.add(card); db.session.flush(); id_map[c.get('id')] = card.id
            db.session.commit()
            for r in reviews:
                old_cid = r.get('card_id'); new_cid = id_map.get(old_cid);
                if not new_cid: continue
                try: when = datetime.fromisoformat(r.get('when'))
                except: when = datetime.utcnow()
                rv = Review(card_id=new_cid, when=when, quality=r.get('quality') or 0, prior_reps=r.get('prior_reps'),
                            prior_interval=r.get('prior_interval'), prior_easiness=r.get('prior_easiness'),
                            new_reps=r.get('new_reps'), new_interval=r.get('new_interval'), new_easiness=r.get('new_easiness'))
                db.session.add(rv)
            db.session.commit()
            return redirect(url_for('create_page'))
        except Exception as e:
            return f"Invalid JSON or import error: {e}", 400
    return render_template('import.html')

@app.route('/api/cards')
def api_cards():
    q = request.args.get('q','').strip()
    query = Flashcard.query.order_by(Flashcard.created_at.desc())
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Flashcard.question.ilike(like), Flashcard.answer.ilike(like)))
    cards = [c.to_dict(include_meta=True) for c in query.all()]
    return jsonify(cards)

@app.route('/api/history/<int:card_id>')
def api_history(card_id):
    revs = Review.query.filter_by(card_id=card_id).order_by(Review.when.desc()).all()
    out = []
    for r in revs:
        out.append({"when": r.when.isoformat(), "quality": r.quality, "prior_reps": r.prior_reps, "prior_interval": r.prior_interval,
                    "prior_easiness": r.prior_easiness, "new_reps": r.new_reps, "new_interval": r.new_interval, "new_easiness": r.new_easiness})
    return jsonify(out)

# Study: render study page with due cards
# Study: render study page with flexible filters (due/all/subject/tag)

@app.route('/study')
def study_page():
    """
    Study page with filters.
    Query params:
      - mode=all        -> intended to include all non-snoozed cards (cards with next_review==None OR next_review <= now)
      - subject=...     -> filter by subject (case-insensitive)
      - tag=...         -> filter by tag substring (case-insensitive)
      - limit=NN        -> max cards to return (default 200)
      - show_snoozed=1  -> if present and truthy, include snoozed cards (next_review in future)
    """

    mode = (request.args.get('mode') or '').lower()
    subject = (request.args.get('subject') or '').strip()
    tag = (request.args.get('tag') or '').strip()
    show_snoozed = request.args.get('show_snoozed')
    try:
        limit = int(request.args.get('limit', 200))
    except Exception:
        limit = 200

    now = datetime.utcnow()

    # Base query: always start from Flashcard table
    q = Flashcard.query

    # By default (and for mode='all'), exclude snoozed cards:
    # include only those with next_review is NULL (never scheduled) OR next_review <= now (due)
    # If the caller explicitly requests show_snoozed=1, include snoozed cards as well.
    if not show_snoozed:
        q = q.filter((Flashcard.next_review == None) | (Flashcard.next_review <= now))

    # If you want a special 'only due' behavior it's already covered by above.
    # Keep subject/tag filters:
    if subject:
        q = q.filter(Flashcard.subject.ilike(f"%{subject}%"))
    if tag:
        q = q.filter(Flashcard.tags.ilike(f"%{tag}%"))

    # Order due cards first (nulls first), then by created time
    cards = q.order_by(Flashcard.next_review.asc().nullsfirst(), Flashcard.created_at.asc()).limit(limit).all()

    # Convert to dicts for the template (ensure your to_dict includes image_filename if needed)
    cards_d = [c.to_dict(include_meta=True) for c in cards]

    # supply list of available subjects & tags (for client-side selector) — unchanged behavior
    all_subjects = sorted({(c.subject or "").strip() for c in Flashcard.query.distinct(Flashcard.subject).all() if (c.subject or "").strip()})
    tags_set = set()
    for c in Flashcard.query.all():
        if c.tags:
            for t in c.tags.split(','):
                t = t.strip()
                if t:
                    tags_set.add(t)
    all_tags = sorted(tags_set)

    # ensure next_review is stringified safely for JSON
    for c in cards_d:
        c['next_review'] = c['next_review'][:19] if c.get('next_review') else None
        c['question_short'] = (c['question'][:140] + '...') if c.get('question') and len(c['question'])>140 else c.get('question')

    return render_template('study.html',
                           cards=cards_d,
                           subjects=all_subjects,
                           tags=all_tags,
                           selected_mode=mode,
                           sel_subject=subject,
                           sel_tag=tag)


@app.route('/study/review', methods=['POST'])
def study_review():
    """
    Accepts JSON:
      { "card_id": <int>, "action": "known"|"unknown"|"snooze", "snooze_days": <int, optional> }
    """
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "invalid or missing JSON payload"}), 400

    cid = payload.get('card_id')
    action = (payload.get('action') or '').lower()
    snooze_days = payload.get('snooze_days', 0)

    # validate card id
    try:
        cid = int(cid)
    except Exception:
        return jsonify({"error": "invalid card_id"}), 400

    card = Flashcard.query.get(cid)
    if not card:
        return jsonify({"error": "card not found"}), 404

    # safe snapshot of existing meta
    prior_reps = getattr(card, 'reps', None)
    prior_interval = getattr(card, 'interval', None)
    prior_easiness = float(getattr(card, 'easiness', 2.5))

    try:
        if action == 'snooze':
            # validate snooze_days
            try:
                snooze_days = int(snooze_days)
            except Exception:
                return jsonify({"error": "invalid snooze_days"}), 400
            if snooze_days <= 0:
                return jsonify({"error": "snooze_days must be > 0"}), 400

            # set next_review to UTC now + snooze_days
            card.next_review = datetime.utcnow() + timedelta(days=snooze_days)
            db.session.add(card)
            db.session.commit()

            # record a Review row if you keep Review history (optional)
            try:
                rv = Review(card_id=card.id, quality=0,
                            prior_reps=prior_reps, prior_interval=prior_interval, prior_easiness=prior_easiness,
                            new_reps=getattr(card, 'reps', None), new_interval=getattr(card, 'interval', None),
                            new_easiness=getattr(card, 'easiness', None))
                db.session.add(rv)
                db.session.commit()
            except Exception:
                # don't fail the request if review history can't be written
                db.session.rollback()

            return jsonify({
                "status": "ok",
                "action": "snooze",
                "card_id": card.id,
                "next_review": card.next_review.isoformat()
            })

        elif action in ('known', 'unknown'):
            # map action to quality (customize if you like)
            quality = 5 if action == 'known' else 2
            # rely on sm2_update existing helper which should update card.* fields
            new_snapshot = sm2_update(card, quality)
            db.session.add(card)
            db.session.commit()

            # record review
            try:
                rv = Review(card_id=card.id, quality=quality,
                            prior_reps=prior_reps, prior_interval=prior_interval, prior_easiness=prior_easiness,
                            new_reps=new_snapshot.get('reps'), new_interval=new_snapshot.get('interval'),
                            new_easiness=new_snapshot.get('easiness'))
                db.session.add(rv)
                db.session.commit()
            except Exception:
                db.session.rollback()

            return jsonify({
                "status": "ok",
                "action": action,
                "card_id": card.id,
                "next_review": card.next_review.isoformat() if card.next_review else None,
                "new_meta": new_snapshot
            })
        else:
            return jsonify({"error": "unknown action"}), 400

    except Exception as e:
        # log server-side error if you have logger, then return 500
        try:
            app.logger.exception("study_review failure")
        except Exception:
            pass
        db.session.rollback()
        return jsonify({"error": "internal server error", "detail": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True)
