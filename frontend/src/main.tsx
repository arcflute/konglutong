import {createRoot} from 'react-dom/client';
import {Component,ErrorInfo,ReactNode} from 'react';
import {AppProvider} from './app/context';
import {App} from './app/App';
import './styles/theme.css';
import './styles/layout.css';
class Boundary extends Component<{children:ReactNode},{error:string}>{state={error:''};static getDerivedStateFromError(e:Error){return {error:e.message}}componentDidCatch(e:Error,info:ErrorInfo){console.error('Application boundary',e.message,info.componentStack)}render(){return this.state.error?<div className="standalone" data-portal="biz"><h1>页面暂时无法显示</h1><p>{this.state.error}</p><button onClick={()=>location.reload()}>重新加载当前页面</button><a href="/">返回门户</a></div>:this.props.children}}
createRoot(document.getElementById('root')!).render(<Boundary><AppProvider><App/></AppProvider></Boundary>);
