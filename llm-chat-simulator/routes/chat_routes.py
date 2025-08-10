from flask import Blueprint, render_template, request, redirect, url_for, session
from models.llm import LLM

chat_bp = Blueprint('chat', __name__)
llm1 = LLM("LLM 1")
llm2 = LLM("LLM 2")

@chat_bp.route('/')
def index():
    return render_template('chat.html')

@chat_bp.route('/send_message', methods=['POST'])
def send_message():
    user_input = request.form['user_input']
    context1 = session.get('context1', '')
    context2 = session.get('context2', '')
    
    llm1_response = llm1.send_message(user_input, context1)
    llm2_response = llm2.send_message(llm1_response, context2)

    return render_template('chat.html', user_input=user_input, llm1_response=llm1_response, llm2_response=llm2_response)

@chat_bp.route('/edit_context', methods=['GET', 'POST'])
def edit_context():
    if request.method == 'POST':
        session['context1'] = request.form['context1']
        session['context2'] = request.form['context2']
        return redirect(url_for('chat.index'))
    
    context1 = session.get('context1', '')
    context2 = session.get('context2', '')
    return render_template('edit_context.html', context1=context1, context2=context2)