from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, date
import calendar
import csv
import math
import json
import io
import atexit
import os

from dotenv import load_dotenv

# Importando o Agendador de Tarefas
from apscheduler.schedulers.background import BackgroundScheduler

# Importando modelos e serviço de drive
from models import db, User, Turma, Aula, ProfessorAdjunto
from drive_service import DriveService

# Carrega variáveis do arquivo .env (se existir)
load_dotenv()

app = Flask(__name__)

# Configurações sensíveis via variáveis de ambiente
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
if not app.config['SECRET_KEY']:
    raise ValueError(
        'SECRET_KEY não definida. Crie ou edite o arquivo .env e defina SECRET_KEY=... '
        '(veja .env.example). Gere uma chave: python -c "import secrets; print(secrets.token_hex(32))"'
    )

app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv(
    'DATABASE_URL',
    'sqlite:///planner.db'
)

db.init_app(app)

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

drive_service = DriveService()

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# ==========================================
# ROTAS DE AUTENTICAÇÃO E DASHBOARD (Mantidas iguais)
# ==========================================
# ... (Omitted code for brevity: register, login, logout, dashboard logic remains the same) ...

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        nome = request.form.get('nome')
        
        user = User.query.filter_by(email=email).first()
        if user:
            flash('Email já cadastrado.', 'error')
            return redirect(url_for('register'))
        
        new_user = User(
            email=email, 
            nome=nome,
            password=generate_password_hash(password, method='pbkdf2:sha256')
        )
        db.session.add(new_user)
        db.session.commit()
        
        login_user(new_user)
        return redirect(url_for('dashboard'))
        
    return render_template('register.html')

def sincronizar_ultimo_backup(user):
    """
    Busca o último backup do usuário no Google Drive e aplica a sincronização
    (importa turmas/aulas que ainda não existem). Retorna (sucesso, mensagem).
    """
    files = drive_service.list_backups()
    if not files:
        return False, None

    for f in files:
        content = drive_service.download_file_content(f['id'])
        if not content:
            continue
        try:
            dados = json.loads(content)
            # Só restaura se o backup for deste usuário (mesmo email)
            if dados.get('email') != user.email:
                continue
            processar_importacao(dados, user.id)
            return True, f.get('name', 'backup')
        except (json.JSONDecodeError, Exception):
            continue
    return False, None


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        user = User.query.filter_by(email=email).first()
        
        if user and check_password_hash(user.password, password):
            login_user(user)
            # Sincronização automática: busca último backup do usuário no Drive
            try:
                ok, nome = sincronizar_ultimo_backup(user)
                if ok:
                    flash(f'Login realizado. Dados sincronizados com o backup "{nome}".', 'success')
                else:
                    flash('Login realizado com sucesso!', 'success')
            except Exception:
                flash('Login realizado com sucesso!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Email ou senha incorretos.', 'error')
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def dashboard():
    # Código do dashboard mantido igual ao original enviado
    view_mode = request.args.get('view', 'semanal')
    try:
        offset = int(request.args.get('offset', 0))
    except ValueError:
        offset = 0

    hoje = datetime.now()
    dias_calendario = []
    
    if view_mode == 'mensal':
        mes_alvo = hoje.month + offset
        ano_alvo = hoje.year + ((mes_alvo - 1) // 12)
        mes_alvo = ((mes_alvo - 1) % 12) + 1
        
        cal = calendar.Calendar(firstweekday=6)
        weeks = cal.monthdatescalendar(ano_alvo, mes_alvo)
        
        for week in weeks:
            for day in week:
                dias_calendario.append({
                    'date': day,
                    'day': day.day,
                    'full_date': day.strftime('%Y-%m-%d'),
                    'in_month': (day.month == mes_alvo)
                })
        
        start_date = dias_calendario[0]['date']
        end_date = dias_calendario[-1]['date']
        meses = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
        current_date_display = f"{meses[mes_alvo-1]} {ano_alvo}"
        
    else:
        data_base = hoje + timedelta(weeks=offset)
        idx_semana = (data_base.weekday() + 1) % 7 
        start_date = (data_base - timedelta(days=idx_semana)).date()
        for i in range(7):
            day = start_date + timedelta(days=i)
            dias_calendario.append({
                'date': day,
                'day': day.day,
                'full_date': day.strftime('%Y-%m-%d'),
                'in_month': True
            })
        end_date = dias_calendario[-1]['date']
        current_date_display = f"Semana de {start_date.strftime('%d/%m')} a {end_date.strftime('%d/%m')}"

    aulas = Aula.query.filter(
        Aula.turma.has(user_id=current_user.id, ativa=True), 
        Aula.data >= start_date,
        Aula.data <= end_date
    ).all()

    layout_data = {}
    for item in dias_calendario:
        layout_data[item['full_date']] = {'Manhã': [], 'Tarde': [], 'Noite': []}

    for aula in aulas:
        d_str = aula.data.strftime('%Y-%m-%d')
        if d_str in layout_data:
            turno = aula.turno if aula.turno in ['Manhã', 'Tarde', 'Noite'] else 'Noite' 
            layout_data[d_str][turno].append(aula)

    turmas = Turma.query.filter_by(user_id=current_user.id, ativa=True).all()
    professores_lista = ProfessorAdjunto.query.filter_by(user_id=current_user.id).all()

    return render_template(
        'dashboard.html',
        view_mode=view_mode,
        offset=offset,
        hoje=hoje,
        dias_calendario=dias_calendario,
        layout_data=layout_data,
        dias_semana=['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb'],
        current_date_display=current_date_display,
        turmas=turmas,
        professores=professores_lista
    )

@app.route('/criar_aula', methods=['POST'])
@login_required
def criar_aula():
    try:
        turma_id = request.form.get('turma_id')
        data_str = request.form.get('data')
        prof_ministrante = request.form.get('ministrante_id')

        ministrante_id_db = None
        if prof_ministrante and prof_ministrante != 'me':
            ministrante_id_db = int(prof_ministrante)

        nova = Aula(
            turma_id=turma_id,
            professor_id=current_user.id,
            ministrante_id=ministrante_id_db,
            titulo=request.form.get('titulo'),
            data=datetime.strptime(data_str, '%Y-%m-%d').date(),
            turno=request.form.get('turno'),
            status=request.form.get('status', 'Planejando'),
            numero_aula=request.form.get('numero_aula', 0),
            sala=request.form.get('sala'),
            unidade_predio=request.form.get('unidade_predio'),
            bloco_estudo=request.form.get('bloco_estudo'),
            descricao=request.form.get('descricao'),
            observacoes=request.form.get('obs'),
            link_arquivos=request.form.get('link_arquivos')
        )
        db.session.add(nova)
        db.session.commit()
        flash('Aula planejada com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao salvar: {str(e)}', 'error')
    return redirect(request.referrer or url_for('dashboard'))

# ==========================================
# GESTÃO DE TURMAS (Códigos mantidos)
# ==========================================
# ... Copiar rotas de turma do arquivo original ...
@app.route('/turmas')
@login_required
def listar_turmas():
    turmas = Turma.query.filter_by(user_id=current_user.id).all()
    return render_template('turmas.html', turmas=turmas)

@app.route('/turmas/nova', methods=['POST'])
@login_required
def criar_turma():
    try:
        is_ativa = True if request.form.get('ativa') else False
        nova = Turma(
            user_id=current_user.id,
            nome=request.form.get('nome'),
            codigo_completo=request.form.get('codigo_completo'),
            unidade_curricular=request.form.get('unidade_curricular'),
            link_diario=request.form.get('link_diario'),
            ativa=is_ativa
        )
        db.session.add(nova)
        db.session.commit()
        flash('Turma criada com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao criar turma: {e}', 'error')
    return redirect(url_for('listar_turmas'))

@app.route('/turmas/editar', methods=['POST'])
@login_required
def editar_turma():
    try:
        turma_id = request.form.get('turma_id')
        turma = db.session.get(Turma, int(turma_id))
        
        if turma and turma.user_id == current_user.id:
            turma.nome = request.form.get('nome')
            turma.codigo_completo = request.form.get('codigo_completo')
            turma.unidade_curricular = request.form.get('unidade_curricular')
            turma.link_diario = request.form.get('link_diario')
            turma.ativa = True if request.form.get('ativa') else False
            
            db.session.commit()
            flash('Turma atualizada!', 'success')
    except Exception as e:
        flash(f'Erro ao editar: {e}', 'error')
    return redirect(url_for('listar_turmas'))

@app.route('/turmas/status/<int:id>')
@login_required
def toggle_status_turma(id):
    turma = db.session.get(Turma, id)
    if turma and turma.user_id == current_user.id:
        turma.ativa = not turma.ativa
        db.session.commit()
        status = "ativada" if turma.ativa else "desativada"
        flash(f'Turma {status} com sucesso.', 'success')
    return redirect(url_for('listar_turmas'))

@app.route('/turmas/excluir/<int:id>')
@login_required
def excluir_turma(id):
    turma = db.session.get(Turma, id)
    if turma and turma.user_id == current_user.id:
        db.session.delete(turma)
        db.session.commit()
        flash('Turma removida.', 'success')
    return redirect(url_for('listar_turmas'))

@app.route('/turmas/imprimir/<int:turma_id>')
@login_required
def imprimir_turma(turma_id):
    turma = db.session.get(Turma, turma_id)
    if not turma or turma.user_id != current_user.id:
        flash('Turma não encontrada ou sem permissão.', 'error')
        return redirect(url_for('gerenciar_aulas'))
    aulas = sorted(turma.aulas, key=lambda x: x.data)
    for index, aula in enumerate(aulas, start=1):
        aula.numero_aula = index
    return render_template('imprimir_turma.html', turma=turma, aulas=aulas, hoje=datetime.now())


# ==========================================
# GESTÃO DE AULAS (Com correções no get_aula e editar)
# ==========================================

@app.route('/get_aula/<int:id>')
@app.route('/aula/detalhes/<int:id>')
@login_required
def get_aula(id):
    aula = db.session.get(Aula, id)
    if not aula or aula.turma.user_id != current_user.id:
        return {'error': 'Acesso negado'}, 403
    return aula.to_json()

@app.route('/aula/editar', methods=['POST'])
@login_required
def editar_aula():
    try:
        aula_id = request.form.get('aula_id')
        aula = db.session.get(Aula, int(aula_id))
        
        if not aula or aula.turma.user_id != current_user.id:
            flash('Erro de permissão.', 'error')
            return redirect(url_for('dashboard'))

        prof_ministrante = request.form.get('ministrante_id')
        ministrante_id_db = None
        if prof_ministrante and prof_ministrante != 'me':
            ministrante_id_db = int(prof_ministrante)
        
        nova_turma_id = request.form.get('turma_id')
        if nova_turma_id:
            aula.turma_id = int(nova_turma_id)

        aula.ministrante_id = ministrante_id_db
        aula.titulo = request.form.get('titulo')
        aula.data = datetime.strptime(request.form.get('data'), '%Y-%m-%d').date()
        aula.turno = request.form.get('turno')
        aula.status = request.form.get('status')
        aula.sala = request.form.get('sala')
        aula.unidade_predio = request.form.get('unidade_predio')
        aula.bloco_estudo = request.form.get('bloco_estudo')
        aula.descricao = request.form.get('descricao')
        aula.link_arquivos = request.form.get('link_arquivos')
        aula.numero_aula = request.form.get('numero_aula') 
        aula.observacoes = request.form.get('observacoes') 
        
        db.session.commit()
        flash('Aula atualizada com sucesso!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao atualizar: {e}', 'error')
        
    return redirect(request.referrer or url_for('dashboard'))

@app.route('/aula/excluir/<int:id>')
@login_required
def excluir_aula(id):
    aula = db.session.get(Aula, id)
    if aula and aula.turma.user_id == current_user.id:
        db.session.delete(aula)
        db.session.commit()
        flash('Aula removida.', 'success')
    return redirect(request.referrer or url_for('dashboard'))

@app.route('/gerenciar_aulas')
@login_required
def gerenciar_aulas():
    page = request.args.get('page', 1, type=int)
    turma_filter = request.args.get('turma_id')
    search_query = request.args.get('search')
    status_filter = request.args.getlist('status')
    
    per_page = 20
    offset = (page - 1) * per_page

    query = Aula.query.join(Turma).filter(Turma.user_id == current_user.id)

    if turma_filter and turma_filter != 'Todas':
        query = query.filter(Aula.turma_id == int(turma_filter))
    
    if search_query:
        query = query.filter(Aula.titulo.contains(search_query))
  
    if status_filter and len(status_filter) > 0:
        query = query.filter(Aula.status.in_(status_filter))

    total_items = query.count()
    total_pages = math.ceil(total_items / per_page)
    
    aulas = query.order_by(Aula.data.asc()).limit(per_page).offset(offset).all()

    todas_turmas = Turma.query.filter_by(user_id=current_user.id, ativa=True).all()
    todos_professores = ProfessorAdjunto.query.filter_by(user_id=current_user.id).all()

    return render_template(
        'gerenciar_aulas.html', 
        aulas=aulas, 
        page=page, 
        total_pages=total_pages, 
        total_items=total_items, 
        view_name='gerenciar', 
        turma_selecionada=turma_filter, 
        search_query=search_query,
        status_selecionados=status_filter,
        turmas=todas_turmas,
        professores=todos_professores
    )


# ==========================================
# IMPORTAÇÃO DE AULAS (CSV)
# ==========================================

@app.route('/aulas/importar/modelo')
@login_required
def download_modelo_importacao():
    """Gera e envia um CSV modelo para importação de aulas."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'turma', 'data', 'titulo', 'turno', 'status', 'sala',
        'unidade_predio', 'bloco_estudo', 'numero_aula', 'descricao', 'observacoes', 'link_arquivos'
    ])
    writer.writerow([
        'Nome da Turma', '2025-02-10', 'Introdução ao tema', 'Manhã', 'Planejando',
        '101', 'Bloco A', 'Bloco 1', '1', 'Conteúdo da aula', 'Obs.', ''
    ])
    output.seek(0)
    mem = io.BytesIO(output.getvalue().encode('utf-8-sig'))
    mem.seek(0)
    return send_file(
        mem,
        as_attachment=True,
        download_name='modelo_importacao_aulas.csv',
        mimetype='text/csv'
    )


@app.route('/aulas/importar', methods=['POST'])
@login_required
def importar_aulas():
    """Recebe um CSV e importa as aulas para as turmas do usuário."""
    if 'arquivo' not in request.files:
        flash('Nenhum arquivo enviado.', 'error')
        return redirect(url_for('gerenciar_aulas'))

    arquivo = request.files['arquivo']
    if arquivo.filename == '':
        flash('Nenhum arquivo selecionado.', 'error')
        return redirect(url_for('gerenciar_aulas'))

    if not arquivo.filename.lower().endswith('.csv'):
        flash('Envie um arquivo CSV.', 'error')
        return redirect(url_for('gerenciar_aulas'))

    turmas_usuario = {t.nome.strip().lower(): t for t in Turma.query.filter_by(user_id=current_user.id).all()}
    if not turmas_usuario:
        flash('Cadastre ao menos uma turma antes de importar aulas.', 'error')
        return redirect(url_for('gerenciar_aulas'))

    try:
        stream = io.StringIO(arquivo.stream.read().decode('utf-8-sig'))
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            flash('CSV inválido ou vazio.', 'error')
            return redirect(url_for('gerenciar_aulas'))

        # Normaliza nomes das colunas (strip e lowercase)
        fieldnames = [f.strip().lower().replace(' ', '_') for f in (reader.fieldnames or [])]
        required = {'turma', 'data', 'titulo'}
        if not required.issubset(set(fieldnames)):
            flash('CSV deve ter as colunas: turma, data, titulo.', 'error')
            return redirect(url_for('gerenciar_aulas'))

        importadas = 0
        erros = []

        for idx, row in enumerate(reader, start=2):
            # Monta dict com chaves normalizadas
            row_norm = {}
            for i, k in enumerate(reader.fieldnames):
                if i < len(fieldnames):
                    row_norm[fieldnames[i]] = (row.get(k) or '').strip()

            turma_nome = (row_norm.get('turma') or '').strip()
            data_str = (row_norm.get('data') or '').strip()
            titulo = (row_norm.get('titulo') or '').strip()

            if not titulo or not data_str or not turma_nome:
                continue

            turma = turmas_usuario.get(turma_nome.lower())
            if not turma:
                erros.append(f'Linha {idx}: turma "{turma_nome}" não encontrada.')
                continue

            try:
                data_dt = datetime.strptime(data_str[:10], '%Y-%m-%d').date()
            except ValueError:
                erros.append(f'Linha {idx}: data inválida "{data_str}". Use AAAA-MM-DD.')
                continue

            turno = (row_norm.get('turno') or 'Noite').strip()
            if turno not in ('Manhã', 'Tarde', 'Noite'):
                turno = 'Noite'
            status = (row_norm.get('status') or 'Planejando').strip()
            if status not in ('Planejando', 'Preparar', 'Pronta', 'Entregue'):
                status = 'Planejando'

            numero_aula = row_norm.get('numero_aula')
            try:
                numero_aula = int(numero_aula) if numero_aula else None
            except (ValueError, TypeError):
                numero_aula = None

            aula = Aula(
                turma_id=turma.id,
                professor_id=current_user.id,
                ministrante_id=None,
                titulo=titulo,
                data=data_dt,
                turno=turno,
                status=status,
                numero_aula=numero_aula,
                sala=row_norm.get('sala') or None,
                unidade_predio=row_norm.get('unidade_predio') or None,
                bloco_estudo=row_norm.get('bloco_estudo') or None,
                descricao=row_norm.get('descricao') or None,
                observacoes=row_norm.get('observacoes') or None,
                link_arquivos=row_norm.get('link_arquivos') or None,
            )
            db.session.add(aula)
            importadas += 1

        db.session.commit()

        if importadas > 0:
            flash(f'{importadas} aula(s) importada(s) com sucesso.', 'success')
        if erros:
            for msg in erros[:10]:
                flash(msg, 'error')
            if len(erros) > 10:
                flash(f'… e mais {len(erros) - 10} erro(s).', 'error')
        if importadas == 0 and not erros:
            flash('Nenhuma linha válida para importar. Verifique o CSV.', 'warning')

    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao importar: {str(e)}', 'error')

    return redirect(url_for('gerenciar_aulas'))


# ==========================================
# CONFIGURAÇÕES E BACKUP (Restante mantido)
# ==========================================
@app.route('/configuracoes')
@login_required
def configuracoes():
    professores = ProfessorAdjunto.query.filter_by(user_id=current_user.id).all()
    return render_template('configuracoes.html', professores=professores)

@app.route('/perfil/atualizar', methods=['POST'])
@login_required
def atualizar_perfil():
    try:
        user = db.session.get(User, current_user.id)
        user.nome = request.form.get('nome')
        user.email = request.form.get('email')
        
        nova_senha = request.form.get('senha')
        if nova_senha:
            user.password = generate_password_hash(nova_senha, method='pbkdf2:sha256')
            
        db.session.commit()
        flash('Perfil atualizado com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao atualizar: {e}', 'error')
    return redirect(url_for('configuracoes'))

@app.route('/professores/adicionar', methods=['POST'])
@login_required
def adicionar_professor():
    nome = request.form.get('nome')
    if nome:
        novo = ProfessorAdjunto(user_id=current_user.id, nome=nome)
        db.session.add(novo)
        db.session.commit()
        flash('Professor cadastrado.', 'success')
    return redirect(url_for('configuracoes'))

@app.route('/professores/excluir/<int:id>')
@login_required
def excluir_professor(id):
    prof = db.session.get(ProfessorAdjunto, id)
    if prof and prof.user_id == current_user.id:
        db.session.delete(prof)
        db.session.commit()
        flash('Professor removido.', 'success')
    return redirect(url_for('configuracoes'))

@app.route('/backup/download')
@login_required
def download_backup():
    data = current_user.to_dict()
    json_str = json.dumps(data, indent=4, ensure_ascii=False)
    mem = io.BytesIO()
    mem.write(json_str.encode('utf-8'))
    mem.seek(0)
    return send_file(mem, as_attachment=True, download_name=f'backup_planner_{datetime.now().strftime("%Y%m%d")}.json', mimetype='application/json')

@app.route('/backup/drive/upload')
@login_required
def upload_drive():
    data = current_user.to_dict()
    json_str = json.dumps(data, indent=4, ensure_ascii=False)
    filename = f"backup_planner_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.json"
    success, msg = drive_service.upload_backup(filename, json_str)
    if success:
        flash(f'Backup "{filename}" enviado para o Google Drive!', 'success')
    else:
        flash(f'Erro ao enviar para o Drive: {msg}', 'error')
    return redirect(url_for('configuracoes'))

@app.route('/backup/drive/list')
@login_required
def list_drive_backups():
    files = drive_service.list_backups()
    return jsonify(files)

@app.route('/backup/drive/restore/<file_id>')
@login_required
def restore_drive(file_id):
    content = drive_service.download_file_content(file_id)
    if not content:
        flash('Erro ao baixar arquivo do Drive.', 'error')
        return redirect(url_for('configuracoes'))
    try:
        dados = json.loads(content)
        processar_importacao(dados, current_user.id)
        flash('Dados restaurados/sincronizados com a nuvem!', 'success')
    except Exception as e:
        flash(f'Erro ao processar backup: {e}', 'error')
    return redirect(url_for('configuracoes'))

def processar_importacao(dados, user_id):
    mapa_ids_turma = {}

    for t in dados.get('turmas', []):
        existente = Turma.query.filter_by(user_id=user_id, nome=t.get('nome')).first()
        if existente:
            mapa_ids_turma[t.get('id')] = existente.id
        else:
            codigo = t.get('codigo_completo')
            uc = t.get('unidade_curricular')
            if not codigo and 'descricao' in t:
                parts = t['descricao'].split('\n')
                for p in parts:
                    if "Turma:" in p: codigo = p.replace("Turma:", "").strip()
                    if "Unidade Curricular:" in p: uc = p.replace("Unidade Curricular:", "").strip()

            nova = Turma(
                user_id=user_id,
                nome=t.get('nome'),
                codigo_completo=codigo,
                unidade_curricular=uc,
                link_diario=t.get('link_diario', ''),
                ativa=t.get('ativa', True)
            )
            db.session.add(nova)
            db.session.commit()
            mapa_ids_turma[t.get('id')] = nova.id

    for a in dados.get('aulas', []):
        turma_id_novo = mapa_ids_turma.get(a.get('turmaId')) or mapa_ids_turma.get(a.get('turma_id'))
        if turma_id_novo:
            data_str = a.get('data')
            if isinstance(data_str, str):
                data_dt = datetime.strptime(data_str[:10], '%Y-%m-%d').date()
            else:
                data_dt = data_str 

            exists = Aula.query.filter_by(turma_id=turma_id_novo, data=data_dt, titulo=a.get('titulo')).first()
            if not exists:
                nova_aula = Aula(
                    turma_id=turma_id_novo,
                    professor_id=user_id,
                    titulo=a.get('titulo'),
                    data=data_dt,
                    turno=a.get('turno', 'Noite'),
                    status=a.get('status', 'Planejando'),
                    sala=a.get('sala'),
                    unidade_predio=a.get('unidade_predio'), # MENTORIA: Mapeado
                    bloco_estudo=a.get('blocoEstudo') or a.get('bloco_estudo'),
                    numero_aula=a.get('numero_aula'), # MENTORIA: Mapeado
                    observacoes=a.get('observacoes'), # MENTORIA: Mapeado
                    descricao=a.get('descricao'),
                    link_arquivos=a.get('linkDrive') or a.get('link_arquivos')
                )
                db.session.add(nova_aula)
    db.session.commit()

# ==========================================
# AGENDADOR DE TAREFAS (CORRIGIDO)
# ==========================================

def realizar_backup_automatico():
    """Função que roda em background, sem usuário logado."""
    # MENTORIA: Adicionei logs melhores para debugging
    timestamp = datetime.now().strftime('%H:%M:%S')
    print(f"--- [{timestamp}] JOB: Iniciando Backup Automático ---")
    
    with app.app_context():
        usuarios = User.query.all()
        for user in usuarios:
            try:
                data = user.to_dict()
                json_str = json.dumps(data, indent=4, ensure_ascii=False)
                filename = f"backup_AUTO_{user.nome}_{datetime.now().strftime('%Y-%m-%d_%Hh%M')}.json"
                
                success, msg = drive_service.upload_backup(filename, json_str)
                if success:
                    print(f"   [OK] {user.nome}")
                else:
                    print(f"   [ERRO] {user.nome}: {msg}")
            except Exception as e:
                print(f"   [CRITICAL] {user.nome}: {e}")

# MENTORIA: Lógica de inicialização do Scheduler protegida
# Isso evita que o scheduler rode 2x quando o Flask está em modo Debug
if __name__ == '__main__':
    scheduler = BackgroundScheduler()
    scheduler.add_job(realizar_backup_automatico, trigger="interval", minutes=60)
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())

    with app.app_context():
        db.create_all()
    
    # use_reloader=False pode ser usado se o scheduler ainda duplicar,
    # mas a proteção de __name__ costuma resolver em produção.
    host = os.getenv('FLASK_HOST', '0.0.0.0')
    port = int(os.getenv('FLASK_PORT', '5000'))
    debug = os.getenv('FLASK_DEBUG', 'false').lower() in ('1', 'true', 'yes')
    app.run(host=host, port=port, debug=debug)
